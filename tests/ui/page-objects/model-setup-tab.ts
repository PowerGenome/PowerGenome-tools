import { Page, Locator, expect } from '@playwright/test';
import { goToWizardStep, loadApp } from './app-startup';

export class ModelSetupTab {
    constructor(private page: Page) { }

    /**
     * Navigate to the web app and open Step 2 (Model Setup)
     */
    async open() {
        await loadApp(this.page, 30000);
        await this.goto();
    }

    /**
     * Navigate to Step 2 on an already-booted app page.
     */
    async goto() {
        await goToWizardStep(this.page, 2);

        // Wait for planning period editor
        await this.page.waitForSelector('#planningPeriodRows .planning-period-row', {
            timeout: 30000
        });
    }

    /**
     * Restore the planning period editor to its default single-row state.
     */
    async resetForTest(): Promise<void> {
        await this.page.evaluate(() => {
            const testWindow = window as Window & {
                resetPlanningPeriodEditorForTests?: () => void;
            };

            testWindow.resetPlanningPeriodEditorForTests?.();
        });
        await this.goto();
        await expect(this.rows()).toHaveCount(1);
    }

    /**
     * Get all planning period rows
     */
    rows(): Locator {
        return this.page.locator('#planningPeriodRows .planning-period-row');
    }

    /**
     * Get Period Start input for a specific row (0-indexed)
     */
    periodStart(index: number): Locator {
        return this.rows().nth(index).locator('.planning-period-start');
    }

    /**
     * Get Planning Year input for a specific row (0-indexed)
     */
    planningYear(index: number): Locator {
        return this.rows().nth(index).locator('.planning-period-model-year');
    }

    /**
     * Get remove button for a specific row (0-indexed)
     */
    removeButton(index: number): Locator {
        return this.rows().nth(index).locator('.planning-period-remove-btn');
    }

    /**
     * Get the "Add planning period" button
     */
    addButton(): Locator {
        return this.page.getByRole('button', { name: 'Add planning period' });
    }

    /**
     * Get the hidden modelYears input (synced by JS)
     */
    hiddenModelYears(): Locator {
        return this.page.locator('#modelYears');
    }

    /**
     * Get the hidden planningYears input (synced by JS)
     */
    hiddenPlanningYears(): Locator {
        return this.page.locator('#planningYears');
    }

    /**
     * Get the validation error element
     */
    validationError(): Locator {
        return this.page.locator('#yearsValidationError');
    }

    /**
     * Helper to add a planning period and wait for it to appear
     */
    async addPlanningPeriod(): Promise<void> {
        const initialCount = await this.rows().count();
        await this.addButton().click();
        await expect(this.rows()).toHaveCount(initialCount + 1);
    }

    /**
     * Helper to get the current year as used by the app
     */
    getCurrentYear(): string {
        return String(new Date().getFullYear());
    }

    /**
     * Helper to wait for validation to complete
     */
    async waitForValidation(): Promise<void> {
        // Small delay to let validation JS run
        await this.page.waitForTimeout(100);
    }

    /**
     * Get a planning period row object with all its inputs
     */
    getRow(index: number) {
        return {
            root: this.rows().nth(index),
            periodStart: this.periodStart(index),
            planningYear: this.planningYear(index),
            remove: this.removeButton(index),
        };
    }
}
