import { test, expect } from '@playwright/test';
import { loadApp } from './page-objects/app-startup';

test.describe('Demand Weighting Dropdown (Step 1)', () => {
    test('demand weight dropdown is visible in clustering mode', async ({ page }) => {
        await loadApp(page, 60000);

        // Step 1 is shown by default
        const dropdown = page.locator('#demandWeightMethod');
        await expect(dropdown).toBeVisible();
    });

    test('demand weight dropdown defaults to none', async ({ page }) => {
        await loadApp(page, 60000);

        const dropdown = page.locator('#demandWeightMethod');
        await expect(dropdown).toHaveValue('none');
    });

    test('demand weight dropdown has all three options', async ({ page }) => {
        await loadApp(page, 60000);

        const dropdown = page.locator('#demandWeightMethod');
        const options = dropdown.locator('option');
        await expect(options).toHaveCount(3);

        const values = await options.evaluateAll((opts: HTMLOptionElement[]) =>
            opts.map(o => o.value)
        );
        expect(values).toContain('none');
        expect(values).toContain('demand-sqrt');
        expect(values).toContain('demand-log');
    });

    test('can change demand weight to sqrt method', async ({ page }) => {
        await loadApp(page, 60000);

        const dropdown = page.locator('#demandWeightMethod');
        await dropdown.selectOption('demand-sqrt');
        await expect(dropdown).toHaveValue('demand-sqrt');
    });

    test('can change demand weight to log method', async ({ page }) => {
        await loadApp(page, 60000);

        const dropdown = page.locator('#demandWeightMethod');
        await dropdown.selectOption('demand-log');
        await expect(dropdown).toHaveValue('demand-log');
    });

    test('demand weight section is hidden when auto-optimize is enabled', async ({ page }) => {
        await loadApp(page, 60000);

        const autoOptimizeCheckbox = page.locator('#autoOptimize');
        const demandSection = page.locator('#demandWeightSection');

        // Initially visible
        await expect(demandSection).toBeVisible();

        // Enable auto-optimize
        await autoOptimizeCheckbox.check();

        // Demand weight section should be hidden
        await expect(demandSection).toBeHidden();
    });

    test('demand weight section reappears when auto-optimize is disabled', async ({ page }) => {
        await loadApp(page, 60000);

        const autoOptimizeCheckbox = page.locator('#autoOptimize');
        const demandSection = page.locator('#demandWeightSection');

        // Enable then disable auto-optimize
        await autoOptimizeCheckbox.check();
        await expect(demandSection).toBeHidden();

        await autoOptimizeCheckbox.uncheck();
        await expect(demandSection).toBeVisible();
    });
});
