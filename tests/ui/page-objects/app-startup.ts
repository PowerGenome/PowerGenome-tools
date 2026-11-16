import { expect, Page } from '@playwright/test';

export async function waitForAppReady(page: Page, timeout = 60000) {
    await expect(page.locator('#loading')).toHaveClass('hidden', { timeout });
}

export async function dismissWelcomeOverlay(page: Page) {
    const welcomeOverlay = page.locator('#welcomeOverlay');
    if (await welcomeOverlay.isVisible()) {
        await welcomeOverlay.getByLabel('Close welcome dialog').click();
        await expect(welcomeOverlay).toHaveClass('hidden');
    }
}

export async function loadApp(page: Page, timeout = 60000) {
    await page.goto('/');
    await waitForAppReady(page, timeout);
    await dismissWelcomeOverlay(page);
}

/**
 * Restart the app in place, keeping the current browser context.
 *
 * A reload re-boots PyScript/Pyodide but reuses the context's HTTP cache, so
 * the jsdelivr-hosted Pyodide, pandas, numpy and shapely files are served
 * locally instead of being downloaded again. That makes it far cheaper than
 * opening a fresh context and calling `loadApp()`, while still resetting the
 * Python-side application state between tests.
 */
export async function reloadApp(page: Page, timeout = 60000) {
    await page.reload();
    await waitForAppReady(page, timeout);
    await dismissWelcomeOverlay(page);
}

export async function goToWizardStep(page: Page, step: number) {
    await page.evaluate((stepNumber) => {
        if (typeof (window as any).goToStep === 'function') {
            (window as any).goToStep(stepNumber);
        }
    }, step);

    await expect(page.locator(`#step-${step}`)).toHaveClass(/active/);
}