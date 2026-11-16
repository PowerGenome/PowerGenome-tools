import { BrowserContext, Page, test as base, expect } from '@playwright/test';
import { loadApp, reloadApp } from '../page-objects/app-startup';

type ResetCallback = (page: Page) => Promise<void>;

type SharedAppSuiteOptions = {
    /**
     * How the shared page is restored before each test.
     *
     * - `'callback'` (default): only `reset` runs. The page keeps its
     *   in-memory state, so a spec that shares a page must reset every value
     *   the previous test could have mutated.
     * - `'reload'`: the page is reloaded inside the existing browser context,
     *   which restarts PyScript/Pyodide and clears all Python-side state
     *   while reusing the context's HTTP cache. Use this when a test mutates
     *   state that has no cheap callback reset. `reset` still runs
     *   afterwards when provided.
     */
    resetMode?: 'callback' | 'reload';
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

    const startupTimeout = options.startupTimeout ?? 30000;
    // Boot/browser work happens in hooks, which have their own timeout.
    const hookTimeout = startupTimeout + 30000;

    let context: BrowserContext | undefined;
    let page: Page | undefined;
    // The load in beforeAll already leaves the page clean, so the first test
    // of a reload-mode suite does not need an extra boot.
    let needsReload = false;

    test.beforeAll(async ({ browser }) => {
        test.setTimeout(hookTimeout);
        context = await browser.newContext();
        page = await context.newPage();
        await loadApp(page, startupTimeout);
        await assertAppReady(page);
        needsReload = false;
    });

    test.afterAll(async () => {
        await context?.close();
        context = undefined;
        page = undefined;
    });

    test.beforeEach(async ({}, testInfo) => {
        const sharedPage = getSharedPage(page);

        if (options.resetMode === 'reload') {
            if (needsReload) {
                // beforeEach shares its timeout with the test body.
                test.setTimeout(testInfo.timeout + startupTimeout);
                await reloadApp(sharedPage, startupTimeout);
                await assertAppReady(sharedPage);
            }
            needsReload = true;
        }

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