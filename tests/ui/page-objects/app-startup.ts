import { expect, Page } from '@playwright/test';

export async function waitForAppReady(page: Page, timeout = 60000) {
    await page.goto('/');
    await expect(page.locator('#loading')).toHaveClass('hidden', { timeout });

    const welcomeOverlay = page.locator('#welcomeOverlay');
    if (await welcomeOverlay.isVisible()) {
        await welcomeOverlay.getByLabel('Close welcome dialog').click();
        await expect(welcomeOverlay).toHaveClass('hidden');
    }
}

export async function goToWizardStep(page: Page, step: number) {
    await page.evaluate((stepNumber) => {
        if (typeof (window as any).goToStep === 'function') {
            (window as any).goToStep(stepNumber);
        }
    }, step);

    await expect(page.locator(`#step-${step}`)).toHaveClass(/active/);
}