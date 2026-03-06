import { BrowserContext, Page, test as base, expect } from '@playwright/test';
import { loadApp } from '../page-objects/app-startup';

type ResetCallback = (page: Page) => Promise<void>;

type SharedAppSuiteOptions = {
    reset?: ResetCallback;
    startupTimeout?: number;
};

type SharedAppSuite = {
    getPage: () => Page;
};

export function createSharedAppSuite(
    test: typeof base,
    options: SharedAppSuiteOptions = {}
): SharedAppSuite {
    test.describe.configure({ mode: 'serial' });

    let context: BrowserContext | undefined;
    let page: Page | undefined;

    test.beforeAll(async ({ browser }) => {
        context = await browser.newContext();
        page = await context.newPage();
        await loadApp(page, options.startupTimeout ?? 30000);
        await assertAppReady(page);
    });

    test.afterAll(async () => {
        await context?.close();
        context = undefined;
        page = undefined;
    });

    test.beforeEach(async () => {
        const sharedPage = getSharedPage(page);
        if (options.reset) {
            await options.reset(sharedPage);
        }
    });

    return {
        getPage: () => getSharedPage(page)
    };
}

export async function assertAppReady(page: Page) {
    await expect(page.locator('#loading')).toHaveClass('hidden');
    await expect(page.locator('#welcomeOverlay')).toHaveClass(/hidden/);
}

function getSharedPage(page: Page | undefined): Page {
    if (!page) {
        throw new Error('Shared Playwright page is not available before suite setup.');
    }

    return page;
}