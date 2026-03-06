import { test, expect, Page } from '@playwright/test';
import { goToWizardStep, waitForAppReady } from './page-objects/app-startup';

/**
 * Helper: navigate to the app, wait for PyScript to initialize, close the welcome
 * overlay, and then jump to Step 4 (New Resources).
 */
async function openStep4(page: Page) {
    await waitForAppReady(page);
    await goToWizardStep(page, 4);

    // Wait for the resource list to be populated with the 6 defaults
    await page.waitForFunction(() => {
        const list = document.getElementById('newResourcesList');
        return list && list.querySelectorAll('.candidate-item').length >= 6;
    }, { timeout: 30000 });
}

test.describe('New Resources — click to populate ATB picker', () => {

    test('resource list contains clickable items with cursor pointer', async ({ page }) => {
        await openStep4(page);

        const items = page.locator('#newResourcesList .candidate-item');
        await expect(items).toHaveCount(6);

        // Each item should have cursor:pointer style indicating it is clickable
        const firstItem = items.first();
        const cursorStyle = await firstItem.evaluate(
            (el) => (el as HTMLElement).style.cursor
        );
        expect(cursorStyle).toBe('pointer');
    });

    test('resource list items have a tooltip describing the click action', async ({ page }) => {
        await openStep4(page);

        const firstItem = page.locator('#newResourcesList .candidate-item').first();
        const title = await firstItem.getAttribute('title');
        expect(title).toContain('ATB picker');
    });

    test('clicking a resource item populates the Technology dropdown', async ({ page }) => {
        await openStep4(page);

        // Click the first resource item (NaturalGas)
        const firstItem = page.locator('#newResourcesList .candidate-item').first();
        await firstItem.click();

        // Technology dropdown should now show NaturalGas
        await expect(page.locator('#atbTechSelect')).toHaveValue('NaturalGas');
    });

    test('clicking a resource item populates the Tech Detail dropdown', async ({ page }) => {
        await openStep4(page);

        const firstItem = page.locator('#newResourcesList .candidate-item').first();
        await firstItem.click();

        // Tech Detail for the first default resource
        await expect(page.locator('#atbTechDetailSelect')).toHaveValue(
            '2-on-1 Combined Cycle (F-Frame)'
        );
    });

    test('clicking a resource item populates the Cost Case dropdown', async ({ page }) => {
        await openStep4(page);

        const firstItem = page.locator('#newResourcesList .candidate-item').first();
        await firstItem.click();

        await expect(page.locator('#atbCostCaseSelect')).toHaveValue('Moderate');
    });

    test('clicking a resource item sets the Size (MW) field', async ({ page }) => {
        await openStep4(page);

        const firstItem = page.locator('#newResourcesList .candidate-item').first();
        await firstItem.click();

        // NaturalGas CC size is 727 MW
        await expect(page.locator('#atbSizeMw')).toHaveValue('727');
    });

    test('clicking a resource item sets the Planning Year to "all"', async ({ page }) => {
        await openStep4(page);

        const firstItem = page.locator('#newResourcesList .candidate-item').first();
        await firstItem.click();

        await expect(page.locator('#newResourceYearSelect')).toHaveValue('all');
    });

    test('clicking a different resource item populates with that resource\'s data', async ({ page }) => {
        await openStep4(page);

        // The default list order: NaturalGas CC, NaturalGas CT, LandbasedWind, UtilityPV, Battery, Nuclear
        // Click the third item (index 2 = LandbasedWind)
        const thirdItem = page.locator('#newResourcesList .candidate-item').nth(2);
        await thirdItem.click();

        await expect(page.locator('#atbTechSelect')).toHaveValue('LandbasedWind');
        await expect(page.locator('#atbTechDetailSelect')).toHaveValue('Class3');
        await expect(page.locator('#atbCostCaseSelect')).toHaveValue('Moderate');
    });

    test('clicking the battery resource expands the override panel with pre-filled values', async ({ page }) => {
        await openStep4(page);

        // Battery is the 5th resource (index 4)
        const batteryItem = page.locator('#newResourcesList .candidate-item').nth(4);
        await batteryItem.click();

        // The battery has variable_o_m_mwh and variable_o_m_mwh_in overrides
        await expect(page.locator('#atbOverrideVarOM')).toHaveValue('0.15');
        await expect(page.locator('#atbOverrideVarOMIn')).toHaveValue('0.15');

        // The override panel should be open
        const overridePanel = page.locator('#atbAttrsOverride');
        const isOpen = await overridePanel.evaluate((el) => (el as HTMLDetailsElement).open);
        expect(isOpen).toBe(true);
    });

    test('clicking a non-battery resource clears the override fields', async ({ page }) => {
        await openStep4(page);

        // First click battery to populate battery overrides
        const batteryItem = page.locator('#newResourcesList .candidate-item').nth(4);
        await batteryItem.click();
        await expect(page.locator('#atbOverrideVarOM')).toHaveValue('0.15');

        // Then click NaturalGas CC (no overrides)
        const ccItem = page.locator('#newResourcesList .candidate-item').first();
        await ccItem.click();

        // Override fields should be cleared
        await expect(page.locator('#atbOverrideVarOM')).toHaveValue('');
        await expect(page.locator('#atbOverrideVarOMIn')).toHaveValue('');
    });

    test('clicking Delete button does not load resource into picker', async ({ page }) => {
        await openStep4(page);

        // Record initial tech value
        const initialTech = await page.locator('#atbTechSelect').inputValue();

        // Click the delete button on the second resource (NaturalGas CT, index 1)
        const deleteBtn = page.locator('#newResourcesList .candidate-item').nth(1)
            .locator('button');
        await deleteBtn.click();

        // The list should now have 5 items
        await expect(page.locator('#newResourcesList .candidate-item')).toHaveCount(5);

        // Tech dropdown should NOT have changed to NaturalGas CT via the row click handler
        // (it stays at whatever it was before the delete)
        const techAfterDelete = await page.locator('#atbTechSelect').inputValue();
        expect(techAfterDelete).toBe(initialTech);
    });

    test('ATB year conflict dialog can be dismissed with its dedicated close button', async ({ page }) => {
        await openStep4(page);

        const yearSelect = page.locator('#atbYearSelect');
        const yearOptions = await yearSelect.locator('option').evaluateAll((options) =>
            options
                .map((option) => (option as HTMLOptionElement).value)
                .filter((value) => value && value !== '2024')
        );

        test.skip(yearOptions.length === 0, 'ATB index only exposes one data year in this environment.');

        await yearSelect.selectOption(yearOptions[0]);
        await page.locator('#addNewResourceBtn').click();

        const conflictOverlay = page.locator('#atbYearConflictOverlay');
        await expect(conflictOverlay).toBeVisible();

        await page.getByLabel('Close ATB conflict dialog').click();
        await expect(conflictOverlay).toHaveClass('hidden');
    });

});
