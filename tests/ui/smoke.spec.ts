import { test, expect } from '@playwright/test';
import { goToWizardStep, waitForAppReady } from './page-objects/app-startup';

test.describe('Smoke Tests', () => {
    test('web app loads and PyScript initializes', async ({ page }) => {
        await waitForAppReady(page, 30000);

        // Wait for the app to load
        await expect(page.locator('#navbar h1')).toContainText('PowerGenome System Design');

        // Check that the main navigation is present
        await expect(page.locator('.nav-steps')).toBeVisible();

        // Verify Step 1 (Regions) is initially active
        await expect(page.locator('#step-1')).toHaveClass(/active/);
    });

    test('can navigate between steps', async ({ page }) => {
        await waitForAppReady(page, 30000);
        await goToWizardStep(page, 2);
        await expect(page.locator('#step-1')).not.toHaveClass(/active/);

        // Verify Step 2 content is visible
        await expect(page.locator('#step-2 h2')).toContainText('Model Setup');
    });

    test('planning period editor initializes', async ({ page }) => {
        await waitForAppReady(page, 30000);
        await goToWizardStep(page, 2);

        // Verify planning period editor components are present
        await expect(page.locator('#planningPeriodEditor')).toBeVisible();
        await expect(page.locator('#planningPeriodRows')).toBeVisible();
        await expect(page.locator('#addPlanningPeriodBtn')).toBeVisible();

        // Should have at least one planning period row
        await expect(page.locator('#planningPeriodRows .planning-period-row')).toHaveCount(1);
    });
});
