import { test, expect } from '@playwright/test';
import { createSharedAppSuite } from './fixtures/shared-app';
import { ModelSetupTab } from './page-objects/model-setup-tab';
import { NewResourcesTab } from './page-objects/new-resources-tab';

async function triggerAtbYearConflict(newResources: NewResourcesTab) {
    await newResources.goto();

    const page = newResources.currentPage();

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
    return conflictOverlay;
}

test.describe('New Resources — click to populate ATB picker', () => {
    const suite = createSharedAppSuite(test, {
        reset: async (page) => {
            const newResources = new NewResourcesTab(page);
            await newResources.resetForTest();
        }
    });

    const newResources = () => new NewResourcesTab(suite.getPage());
    const page = () => suite.getPage();

    test('resource list contains clickable items with cursor pointer', async () => {
        await newResources().goto();

        const items = newResources().items();
        await expect(items).toHaveCount(6);

        // Each item should have cursor:pointer style indicating it is clickable
        const firstItem = items.first();
        const cursorStyle = await firstItem.evaluate(
            (el) => (el as HTMLElement).style.cursor
        );
        expect(cursorStyle).toBe('pointer');
    });

    test('resource list items have a tooltip describing the click action', async () => {
        await newResources().goto();

        const firstItem = newResources().item(0);
        const title = await firstItem.getAttribute('title');
        expect(title).toContain('ATB picker');
    });

    test('clicking a resource item populates the Technology dropdown', async () => {
        await newResources().goto();

        // Click the first resource item (NaturalGas)
        const firstItem = newResources().item(0);
        await firstItem.click();

        // Technology dropdown should now show NaturalGas
        await expect(page().locator('#atbTechSelect')).toHaveValue('NaturalGas');
    });

    test('clicking a resource item populates the Tech Detail dropdown', async () => {
        await newResources().goto();

        const firstItem = newResources().item(0);
        await firstItem.click();

        // Tech Detail for the first default resource
        await expect(page().locator('#atbTechDetailSelect')).toHaveValue(
            '2-on-1 Combined Cycle (F-Frame)'
        );
    });

    test('clicking a resource item populates the Cost Case dropdown', async () => {
        await newResources().goto();

        const firstItem = newResources().item(0);
        await firstItem.click();

        await expect(page().locator('#atbCostCaseSelect')).toHaveValue('Moderate');
    });

    test('clicking a resource item sets the Size (MW) field', async () => {
        await newResources().goto();

        const firstItem = newResources().item(0);
        await firstItem.click();

        // NaturalGas CC size is 727 MW
        await expect(page().locator('#atbSizeMw')).toHaveValue('727');
    });

    test('clicking a resource item sets the Planning Year to "all"', async () => {
        await newResources().goto();

        const firstItem = newResources().item(0);
        await firstItem.click();

        await expect(page().locator('#newResourceYearSelect')).toHaveValue('all');
    });

    test('clicking a different resource item populates with that resource\'s data', async () => {
        await newResources().goto();

        // The default list order: NaturalGas CC, NaturalGas CT, LandbasedWind, UtilityPV, Battery, Nuclear
        // Click the third item (index 2 = LandbasedWind)
        const thirdItem = newResources().item(2);
        await thirdItem.click();

        await expect(page().locator('#atbTechSelect')).toHaveValue('LandbasedWind');
        await expect(page().locator('#atbTechDetailSelect')).toHaveValue('Class3');
        await expect(page().locator('#atbCostCaseSelect')).toHaveValue('Moderate');
    });

    test('clicking the battery resource expands the override panel with pre-filled values', async () => {
        await newResources().goto();

        // Battery is the 5th resource (index 4)
        const batteryItem = newResources().item(4);
        await batteryItem.click();

        // The battery has variable_o_m_mwh and variable_o_m_mwh_in overrides
        await expect(page().locator('#atbOverrideVarOM')).toHaveValue('0.15');
        await expect(page().locator('#atbOverrideVarOMIn')).toHaveValue('0.15');

        // The override panel should be open
        const overridePanel = newResources().overridePanel();
        const isOpen = await overridePanel.evaluate((el) => (el as HTMLDetailsElement).open);
        expect(isOpen).toBe(true);
    });

    test('clicking a non-battery resource clears the override fields', async () => {
        await newResources().goto();

        // First click battery to populate battery overrides
        const batteryItem = newResources().item(4);
        await batteryItem.click();
        await expect(page().locator('#atbOverrideVarOM')).toHaveValue('0.15');

        // Then click NaturalGas CC (no overrides)
        const ccItem = newResources().item(0);
        await ccItem.click();

        // Override fields should be cleared
        await expect(page().locator('#atbOverrideVarOM')).toHaveValue('');
        await expect(page().locator('#atbOverrideVarOMIn')).toHaveValue('');
    });

    test('clicking Delete button does not load resource into picker', async () => {
        await newResources().goto();

        // Record initial tech value
        const initialTech = await page().locator('#atbTechSelect').inputValue();

        // Click the delete button on the second resource (NaturalGas CT, index 1)
        const deleteBtn = newResources().item(1).locator('button');
        await deleteBtn.click();

        // The list should now have 5 items
        await expect(newResources().items()).toHaveCount(5);

        // Tech dropdown should NOT have changed to NaturalGas CT via the row click handler
        // (it stays at whatever it was before the delete)
        const techAfterDelete = await page().locator('#atbTechSelect').inputValue();
        expect(techAfterDelete).toBe(initialTech);
    });

    test('ATB year conflict dialog can be dismissed with its dedicated close button', async () => {
        const conflictOverlay = await triggerAtbYearConflict(newResources());

        await page().getByLabel('Close ATB conflict dialog').click();
        await expect(conflictOverlay).toHaveClass('hidden');
    });

    test('ATB year conflict dialog exposes its description and focuses OK when opened', async () => {
        const conflictOverlay = await triggerAtbYearConflict(newResources());
        await expect(conflictOverlay).toHaveAttribute('aria-describedby', 'atbYearConflictMessage');
        await expect(conflictOverlay.getByRole('button', { name: 'OK' })).toBeFocused();
    });

    test('reset hook restores the default Step 4 baseline after dirty UI state', async () => {
        await newResources().goto();

        const batteryItem = newResources().item(4);
        await batteryItem.click();
        await expect(newResources().overridePanel()).toHaveJSProperty('open', true);

        await page().locator('#newResourceYearSelect').selectOption('2030');
        await page().locator('#atbOverrideCapex').fill('123');
        await newResources().item(1).locator('button').click();
        await expect(newResources().items()).toHaveCount(5);

        await newResources().resetForTest();

        await expect(newResources().items()).toHaveCount(6);
        await expect(newResources().overridePanel()).toHaveJSProperty('open', false);
        await expect(page().locator('#atbOverrideCapex')).toHaveValue('');
        await expect(page().locator('#atbOverrideVarOM')).toHaveValue('');
        await expect(page().locator('#atbOverrideVarOMIn')).toHaveValue('');
        await expect(page().locator('#newResourceYearSelect')).toHaveValue('all');
        await expect(page().locator('#atbYearConflictOverlay')).toHaveClass('hidden');
        await expect(page().locator('#newResourceYearWarning')).toBeHidden();

        await expect.poll(async () => newResources().labels()).toEqual([
            'NaturalGas',
            'NaturalGas',
            'LandbasedWind',
            'UtilityPV',
            'Utility-Scale Battery Storage',
            'Nuclear'
        ]);
    });

    test('Step 4 planning-year options follow Step 2 changes and reset cleanly', async () => {
        const modelSetup = new ModelSetupTab(page());

        await modelSetup.resetForTest();
        const firstRow = modelSetup.getRow(0);
        await firstRow.planningYear.fill('2035');

        await newResources().goto();
        await expect(page().locator('#newResourceYearSelect')).toContainText('2035');

        await modelSetup.resetForTest();
        await newResources().resetForTest();

        const values = await page().locator('#newResourceYearSelect option').evaluateAll((options) =>
            options.map((option) => (option as HTMLOptionElement).value)
        );
        expect(values).toEqual(['all', '2030']);
    });

    test('Step 4 reset remains idempotent across shared-page runs', async () => {
        await newResources().resetForTest();
        await newResources().resetForTest();

        await expect(newResources().items()).toHaveCount(6);
        await expect(newResources().overridePanel()).toHaveJSProperty('open', false);
        await expect(page().locator('#newResourceYearSelect')).toHaveValue('all');
    });

});
