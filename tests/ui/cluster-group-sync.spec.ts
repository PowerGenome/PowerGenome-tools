import { test, expect, Page } from '@playwright/test';
import { createSharedAppSuite } from './fixtures/shared-app';

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Open a collapsible section by clicking its header (if still hidden).
 */
async function openCollapsible(page: Page, contentId: string) {
    const content = page.locator(`#${contentId}`);
    if (await content.evaluate(el => el.classList.contains('hidden'))) {
        await content.locator('xpath=..').locator('.collapsible-header').click();
    }
    await expect(content).not.toHaveClass(/hidden/);
}

/**
 * Wait until the Python/PyScript code has inserted real checkboxes
 * (replacing the initial "Loading..." placeholder).
 */
async function waitForCheckboxes(page: Page, containerId: string) {
    await page.waitForFunction(
        (id) => {
            const el = document.getElementById(id);
            return el !== null && el.querySelectorAll('input[type="checkbox"]').length > 0;
        },
        containerId,
        { timeout: 30_000 }
    );
}

/** Get the checkbox for a specific group value in one of the two lists. */
function groupCheckbox(page: Page, name: 'noCluster' | 'forceCluster', value: string) {
    const containerId = name === 'noCluster' ? 'noClusterContainer' : 'forceClusterContainer';
    return page.locator(`#${containerId} input[name="${name}"][value="${value}"]`);
}

async function expandAndWaitForBothLists(page: Page) {
    await openCollapsible(page, 'noClusterContent');
    await openCollapsible(page, 'forceClusterContent');
    await waitForCheckboxes(page, 'noClusterContainer');
    await waitForCheckboxes(page, 'forceClusterContainer');
}

/** Reset all checkboxes and re-enable any disabled counterparts. */
async function uncheckAll(page: Page) {
    await page.evaluate(() => {
        document.querySelectorAll<HTMLInputElement>(
            '#noClusterContainer input[type="checkbox"]:checked, ' +
            '#forceClusterContainer input[type="checkbox"]:checked'
        ).forEach(cb => {
            cb.checked = false;
            cb.dispatchEvent(new Event('change', { bubbles: true }));
        });
        // Re-enable anything left disabled by the sync
        document.querySelectorAll<HTMLInputElement>(
            '#noClusterContainer input[type="checkbox"]:disabled, ' +
            '#forceClusterContainer input[type="checkbox"]:disabled'
        ).forEach(cb => {
            cb.disabled = false;
            const lbl = cb.closest('label') as HTMLElement | null;
            if (lbl) { lbl.style.opacity = ''; lbl.style.cursor = ''; }
        });
    });
}

// ── Suite ─────────────────────────────────────────────────────────────────────

test.describe('Group checkbox mutual exclusion (noCluster ↔ forceCluster)', () => {
    const suite = createSharedAppSuite(test, {
        reset: async (page) => {
            await expandAndWaitForBothLists(page);
            await uncheckAll(page);
        },
        startupTimeout: 60_000,
    });

    const p = () => suite.getPage();

    test.beforeAll(async () => {
        // Open both panels once; reset() handles them before each individual test.
        await expandAndWaitForBothLists(p());
    });

    // ── Checking noCluster disables same value in forceCluster ────────────────

    test('checking a group in "Keep Unclustered" disables it in "Cluster Together"', async () => {
        const noClusterCB = groupCheckbox(p(), 'noCluster', 'CAISO');
        const forceClusterCB = groupCheckbox(p(), 'forceCluster', 'CAISO');

        await expect(noClusterCB).not.toBeChecked();
        await expect(forceClusterCB).not.toBeDisabled();

        await noClusterCB.check();

        await expect(noClusterCB).toBeChecked();
        await expect(forceClusterCB).toBeDisabled();
    });

    test('disabled counterpart label is visually greyed out', async () => {
        const noClusterCB = groupCheckbox(p(), 'noCluster', 'CAISO');
        await noClusterCB.check();

        const forceLabel = p()
            .locator('#forceClusterContainer input[name="forceCluster"][value="CAISO"]')
            .locator('xpath=ancestor::label[1]');

        const opacity = await forceLabel.evaluate(el => (el as HTMLElement).style.opacity);
        expect(parseFloat(opacity || '1')).toBeLessThan(1);
    });

    // ── Checking forceCluster disables same value in noCluster ───────────────

    test('checking a group in "Cluster Together" disables it in "Keep Unclustered"', async () => {
        const forceClusterCB = groupCheckbox(p(), 'forceCluster', 'ERCOT');
        const noClusterCB = groupCheckbox(p(), 'noCluster', 'ERCOT');

        await expect(forceClusterCB).not.toBeChecked();
        await expect(noClusterCB).not.toBeDisabled();

        await forceClusterCB.check();

        await expect(forceClusterCB).toBeChecked();
        await expect(noClusterCB).toBeDisabled();
    });

    // ── Unchecking re-enables the counterpart ─────────────────────────────────

    test('unchecking in "Keep Unclustered" re-enables it in "Cluster Together"', async () => {
        const noClusterCB = groupCheckbox(p(), 'noCluster', 'CAISO');
        const forceClusterCB = groupCheckbox(p(), 'forceCluster', 'CAISO');

        await noClusterCB.check();
        await expect(forceClusterCB).toBeDisabled();

        await noClusterCB.uncheck();
        await expect(forceClusterCB).not.toBeDisabled();
    });

    test('unchecking in "Cluster Together" re-enables it in "Keep Unclustered"', async () => {
        const forceClusterCB = groupCheckbox(p(), 'forceCluster', 'ISONE');
        const noClusterCB = groupCheckbox(p(), 'noCluster', 'ISONE');

        await forceClusterCB.check();
        await expect(noClusterCB).toBeDisabled();

        await forceClusterCB.uncheck();
        await expect(noClusterCB).not.toBeDisabled();
    });

    // ── Disabled counterpart cannot be toggled ────────────────────────────────

    test('a disabled counterpart checkbox cannot be checked', async () => {
        const noClusterCB = groupCheckbox(p(), 'noCluster', 'NYISO');
        const forceClusterCB = groupCheckbox(p(), 'forceCluster', 'NYISO');

        await noClusterCB.check();
        await expect(forceClusterCB).toBeDisabled();

        // Directly clicking a disabled input should not check it
        await forceClusterCB.evaluate(cb => { (cb as HTMLInputElement).click(); });
        await expect(forceClusterCB).not.toBeChecked();
    });

    // ── Multiple independent groups across both lists ─────────────────────────

    test('different groups can be checked in each list without interfering', async () => {
        const noCB_CAISO = groupCheckbox(p(), 'noCluster', 'CAISO');
        const forceCB_ERCOT = groupCheckbox(p(), 'forceCluster', 'ERCOT');

        await noCB_CAISO.check();
        await forceCB_ERCOT.check();

        await expect(noCB_CAISO).toBeChecked();
        await expect(forceCB_ERCOT).toBeChecked();

        // Their counterparts are disabled …
        await expect(groupCheckbox(p(), 'forceCluster', 'CAISO')).toBeDisabled();
        await expect(groupCheckbox(p(), 'noCluster', 'ERCOT')).toBeDisabled();

        // … but unrelated entries in either list remain enabled
        await expect(groupCheckbox(p(), 'noCluster', 'ISONE')).not.toBeDisabled();
        await expect(groupCheckbox(p(), 'forceCluster', 'ISONE')).not.toBeDisabled();
    });
});

