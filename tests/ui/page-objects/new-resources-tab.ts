import { expect, Locator, Page } from '@playwright/test';
import { goToWizardStep, loadApp } from './app-startup';

export class NewResourcesTab {
    constructor(private page: Page) { }

    currentPage(): Page {
        return this.page;
    }

    async open() {
        await loadApp(this.page, 30000);
        await this.goto();
    }

    async goto() {
        await goToWizardStep(this.page, 4);
        await this.waitForDefaults();
    }

    async resetForTest(): Promise<void> {
        await this.page.evaluate(() => {
            const testWindow = window as Window & {
                resetNewResourcesForTests?: () => void;
            };

            testWindow.resetNewResourcesForTests?.();
        });

        await this.goto();
        await expect(this.items()).toHaveCount(6);
    }

    async waitForDefaults() {
        await this.page.waitForFunction(() => {
            const list = document.getElementById('newResourcesList');
            return list && list.querySelectorAll('.candidate-item').length >= 6;
        }, { timeout: 30000 });
    }

    items(): Locator {
        return this.page.locator('#newResourcesList .candidate-item');
    }

    item(index: number): Locator {
        return this.items().nth(index);
    }

    labels(): Promise<string[]> {
        return this.page.locator('#newResourcesList .candidate-item strong').allInnerTexts();
    }

    overridePanel(): Locator {
        return this.page.locator('#atbAttrsOverride');
    }
}