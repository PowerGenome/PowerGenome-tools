import { test, expect } from '@playwright/test';
import { createSharedAppSuite } from './fixtures/shared-app';
import { ModelSetupTab } from './page-objects/model-setup-tab';

test.describe('Model Setup - Planning Periods', () => {
    const suite = createSharedAppSuite(test, {
        reset: async (page) => {
            const modelSetup = new ModelSetupTab(page);
            await modelSetup.resetForTest();
        }
    });

    const modelSetup = () => new ModelSetupTab(suite.getPage());

    test('renders with default values', async () => {
        const currentYear = modelSetup().getCurrentYear();
        const firstRow = modelSetup().getRow(0);

        // Should have exactly one row initially
        await expect(modelSetup().rows()).toHaveCount(1);

        // First row should have current year and 2030 as defaults
        await expect(firstRow.periodStart).toHaveValue(currentYear);
        await expect(firstRow.planningYear).toHaveValue('2030');

        // Hidden fields should be synced
        await expect(modelSetup().hiddenModelYears()).toHaveValue('2030');
        await expect(modelSetup().hiddenPlanningYears()).toHaveValue(currentYear);

        // No validation errors initially
        await expect(modelSetup().validationError()).toBeHidden();
    });

    test('adding a second period auto-populates the next start year', async () => {
        await modelSetup().addPlanningPeriod();

        const secondRow = modelSetup().getRow(1);

        // Second row should auto-populate Period Start based on first Planning Year (2030)
        await expect(secondRow.periodStart).toHaveValue('2031');
        await expect(secondRow.planningYear).toHaveValue('');

        // Should now have 2 rows
        await expect(modelSetup().rows()).toHaveCount(2);
    });

    test('changing the previous planning year updates the suggested next start year', async () => {
        await modelSetup().addPlanningPeriod();

        const firstRow = modelSetup().getRow(0);
        const secondRow = modelSetup().getRow(1);

        // Change first planning year to 2035
        await firstRow.planningYear.fill('2035');

        // Second row Period Start should update to 2036
        await expect(secondRow.periodStart).toHaveValue('2036');
    });

    test('later period start values remain editable after auto-fill', async () => {
        await modelSetup().addPlanningPeriod();

        const secondRow = modelSetup().getRow(1);
        const currentYear = modelSetup().getCurrentYear();

        // Verify initial auto-fill
        await expect(secondRow.periodStart).toHaveValue('2031');

        // Fill in planning year
        await secondRow.planningYear.fill('2035');

        // Override the auto-filled Period Start
        await secondRow.periodStart.fill('2030');

        // Verify the manual edit sticks
        await expect(secondRow.periodStart).toHaveValue('2030');

        // Hidden fields should sync with manual values
        await expect(modelSetup().hiddenModelYears()).toHaveValue('2030, 2035');
        await expect(modelSetup().hiddenPlanningYears()).toHaveValue(`${currentYear}, 2030`);
    });

    test('incomplete rows surface validation errors', async () => {
        // Use the default row for more predictable validation
        const firstRow = modelSetup().getRow(0);

        // Clear the default period start, leaving only planning year (2030)
        await firstRow.periodStart.clear();

        // Trigger validation by causing an input event
        await firstRow.periodStart.focus();
        await firstRow.periodStart.blur();

        // Wait for validation to process
        await modelSetup().waitForValidation();

        // Should show validation error since we have planning year but no period start
        await expect(modelSetup().validationError()).toBeVisible();
        await expect(modelSetup().validationError()).toContainText('Complete both years');
    });

    test('period start cannot exceed planning year', async () => {
        const firstRow = modelSetup().getRow(0);

        // Set period start after planning year
        await firstRow.periodStart.fill('2031');
        await firstRow.planningYear.fill('2030');
        await modelSetup().waitForValidation();

        // Should show validation error
        await expect(modelSetup().validationError()).toBeVisible();
        await expect(modelSetup().validationError()).toContainText('must be less than or equal');
    });

    test('can remove planning periods', async () => {
        await modelSetup().addPlanningPeriod();
        await modelSetup().addPlanningPeriod();

        // Should have 3 rows
        await expect(modelSetup().rows()).toHaveCount(3);

        // Remove middle row
        const secondRow = modelSetup().getRow(1);
        await secondRow.remove.click();

        // Should have 2 rows
        await expect(modelSetup().rows()).toHaveCount(2);
    });

    test('remove button is hidden when only one row exists', async () => {
        const firstRow = modelSetup().getRow(0);

        // First row remove button should not be visible when it's the only row
        await expect(firstRow.remove).not.toHaveClass(/is-visible/);

        // Add a second row
        await modelSetup().addPlanningPeriod();

        // Now both remove buttons should be visible
        await expect(firstRow.remove).toHaveClass(/is-visible/);
        await expect(modelSetup().getRow(1).remove).toHaveClass(/is-visible/);
    });

    test('hidden inputs sync correctly with multiple periods', async () => {
        const currentYear = modelSetup().getCurrentYear();

        // Add two more periods
        await modelSetup().addPlanningPeriod();
        await modelSetup().addPlanningPeriod();

        const secondRow = modelSetup().getRow(1);
        const thirdRow = modelSetup().getRow(2);

        // Fill in values
        await secondRow.planningYear.fill('2035');
        await thirdRow.planningYear.fill('2040');

        await modelSetup().waitForValidation();

        // Hidden fields should contain all values
        await expect(modelSetup().hiddenModelYears()).toHaveValue('2030, 2035, 2040');
        await expect(modelSetup().hiddenPlanningYears()).toHaveValue(`${currentYear}, 2031, 2036`);
    });

    test('validation clears when errors are fixed', async () => {
        const firstRow = modelSetup().getRow(0);

        // Create validation error
        await firstRow.periodStart.fill('2031');
        await firstRow.planningYear.fill('2030');
        await modelSetup().waitForValidation();

        // Should show error
        await expect(modelSetup().validationError()).toBeVisible();

        // Fix the error
        await firstRow.periodStart.fill('2025');
        await modelSetup().waitForValidation();

        // Error should be gone
        await expect(modelSetup().validationError()).toBeHidden();
    });

    test('reset hook remains idempotent across shared-page runs', async () => {
        const currentYear = modelSetup().getCurrentYear();

        await modelSetup().resetForTest();
        await modelSetup().resetForTest();
        await modelSetup().addPlanningPeriod();

        await expect(modelSetup().rows()).toHaveCount(2);
        await expect(modelSetup().hiddenModelYears()).toHaveValue('2030');
        await expect(modelSetup().hiddenPlanningYears()).toHaveValue(currentYear);
    });
});
