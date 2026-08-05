/**
 * Playwright tests for standalone workflow_state.yml import.
 *
 * Strategy:
 *   - Each test loads a fresh page via loadApp() (which boots PyScript,
 *     dismisses the welcome overlay, and confirms the app is ready).
 *   - The #uploadWorkflowInput file input lives inside #welcomeOverlay.
 *     After loadApp() the overlay is hidden, but Playwright's setInputFiles
 *     works on hidden elements, so we target it directly by selector and
 *     trigger the PyScript change handler without needing the overlay visible.
 *   - Manifest bytes are serialised as JSON; yaml.safe_load on the Python
 *     side accepts JSON because YAML is a superset of JSON.
 *   - We wait for #statusBox to reflect the import result before asserting.
 */

import { test, expect, Page } from '@playwright/test';
import { loadApp, goToWizardStep } from './page-objects/app-startup';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SCHEMA = 'powergenome-tools-workflow-state';
const VERSION = 1;

// ---------------------------------------------------------------------------
// Manifest builders
// ---------------------------------------------------------------------------

interface PlanningPeriod {
    period_start: string | number;
    planning_year: string | number;
    start_mode?: string;
    autofill_value?: string;
}

function minimalManifest(overrides: {
    forms?: Record<string, unknown>;
    state?: Record<string, unknown>;
    tables?: Record<string, unknown>;
    required_supplemental_files?: string[];
    schema?: string;
    version?: number;
} = {}): Record<string, unknown> {
    return {
        schema: overrides.schema ?? SCHEMA,
        version: overrides.version ?? VERSION,
        required_supplemental_files: overrides.required_supplemental_files ?? [],
        forms: overrides.forms ?? {},
        state: overrides.state ?? {},
        tables: overrides.tables ?? {},
    };
}

/**
 * Serialise a manifest as JSON bytes.
 * yaml.safe_load accepts JSON because YAML is a strict superset of JSON,
 * so no additional serialisation library is needed here.
 */
function manifestBytes(manifest: Record<string, unknown>): Buffer {
    return Buffer.from(JSON.stringify(manifest), 'utf-8');
}

// ---------------------------------------------------------------------------
// Upload helper
// ---------------------------------------------------------------------------

/**
 * Upload `content` as a workflow_state.yml file via the (possibly hidden)
 * #uploadWorkflowInput and wait for #statusBox to settle past the transient
 * "Importing…" message.  Returns the final statusBox text.
 */
async function uploadWorkflowYml(page: Page, content: Buffer): Promise<string> {
    // setInputFiles works on hidden/non-visible elements.
    await page.locator('#uploadWorkflowInput').setInputFiles({
        name: 'workflow_state.yml',
        mimeType: 'application/x-yaml',
        buffer: content,
    });

    // PyScript runs the import asynchronously.  Wait until the transient
    // "Importing…" banner disappears (or was never shown) before reading
    // the final status.
    await expect(page.locator('#statusBox')).not.toContainText('Importing workflow', {
        timeout: 30_000,
    });

    return (await page.locator('#statusBox').textContent()) ?? '';
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('Workflow import — standalone workflow_state.yml', () => {

    test('success status shown after importing a minimal valid manifest', async ({ page }) => {
        await loadApp(page, 60_000);

        const statusText = await uploadWorkflowYml(page, manifestBytes(minimalManifest()));

        expect(statusText).toContain('Workflow defaults imported successfully.');
        await expect(page.locator('#statusBox')).toHaveClass(/success/);
    });

    test('planning period rows restored from forms.planning_periods', async ({ page }) => {
        await loadApp(page, 60_000);

        const periods: PlanningPeriod[] = [
            { period_start: 2025, planning_year: 2030, start_mode: 'manual', autofill_value: '' },
            { period_start: 2031, planning_year: 2040, start_mode: 'manual', autofill_value: '' },
        ];

        const manifest = minimalManifest({
            forms: { planning_periods: periods },
        });

        await uploadWorkflowYml(page, manifestBytes(manifest));

        // Navigate to Model Setup (step 2) where the planning period editor lives.
        await goToWizardStep(page, 2);

        const rows = page.locator('#planningPeriodRows .planning-period-row');
        await expect(rows).toHaveCount(2, { timeout: 10_000 });

        await expect(rows.nth(0).locator('.planning-period-start')).toHaveValue('2025');
        await expect(rows.nth(0).locator('.planning-period-model-year')).toHaveValue('2030');

        await expect(rows.nth(1).locator('.planning-period-start')).toHaveValue('2031');
        await expect(rows.nth(1).locator('.planning-period-model-year')).toHaveValue('2040');
    });

    test('hidden modelYears and planningYears inputs reflect imported planning periods', async ({ page }) => {
        await loadApp(page, 60_000);

        const periods: PlanningPeriod[] = [
            { period_start: 2026, planning_year: 2035, start_mode: 'manual', autofill_value: '' },
        ];

        const manifest = minimalManifest({
            forms: { planning_periods: periods },
        });

        await uploadWorkflowYml(page, manifestBytes(manifest));

        await goToWizardStep(page, 2);

        // syncPlanningPeriodInputs() is called by restorePlanningPeriods()
        // and keeps the hidden inputs in sync.
        await expect(page.locator('#modelYears')).toHaveValue('2035', { timeout: 10_000 });
        await expect(page.locator('#planningYears')).toHaveValue('2026', { timeout: 10_000 });
    });

    test('error status shown for an invalid manifest (wrong schema)', async ({ page }) => {
        await loadApp(page, 60_000);

        const manifest = minimalManifest({ schema: 'not-a-valid-schema' });
        const statusText = await uploadWorkflowYml(page, manifestBytes(manifest));

        expect(statusText).toContain('Workflow import error');
        await expect(page.locator('#statusBox')).toHaveClass(/error/);
    });

    test('error status shown when manifest requires supplemental files', async ({ page }) => {
        // A standalone .yml that lists required_supplemental_files must be
        // rejected — the user must upload the full ZIP instead.
        await loadApp(page, 60_000);

        const manifest = minimalManifest({
            required_supplemental_files: ['resource_groups/groups.json'],
        });
        const statusText = await uploadWorkflowYml(page, manifestBytes(manifest));

        expect(statusText).toContain('Workflow import error');
        await expect(page.locator('#statusBox')).toHaveClass(/error/);
    });

    test('error status shown for invalid (non-YAML) file content', async ({ page }) => {
        await loadApp(page, 60_000);

        // Raw bytes that are neither valid UTF-8 nor valid YAML.
        const garbage = Buffer.from([0xff, 0xfe, 0x00, 0x80]);
        const statusText = await uploadWorkflowYml(page, garbage);

        expect(statusText).toContain('Workflow import error');
        await expect(page.locator('#statusBox')).toHaveClass(/error/);
    });

    test('welcome overlay can be re-shown and the import input is then visible', async ({ page }) => {
        // Verifies the overlay/input relationship: after loadApp hides the
        // overlay, we can re-expose it and the file input becomes visible.
        await loadApp(page, 60_000);

        await page.evaluate(() => {
            document.getElementById('welcomeOverlay')?.classList.remove('hidden');
        });

        await expect(page.locator('#welcomeOverlay')).not.toHaveClass(/hidden/);
        await expect(page.locator('#uploadWorkflowInput')).toBeVisible();

        // Dismiss via the close button (same as the user would).
        await page.locator('#welcomeOverlay').getByLabel('Close welcome dialog').click();
        await expect(page.locator('#welcomeOverlay')).toHaveClass(/hidden/);
    });
});
