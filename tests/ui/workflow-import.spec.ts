/**
 * Playwright tests for standalone workflow_state.yml import.
 *
 * Strategy:
 *   - All tests share one booted app via createSharedAppSuite(). Imports
 *     mutate Python-side workflow state and the #statusBox, so the suite
 *     reloads the page before each test: a reload restarts PyScript/Pyodide
 *     inside the same browser context (and its HTTP cache), which is much
 *     cheaper than booting a fresh context per test while still giving every
 *     test a clean app.
 *   - The #uploadWorkflowInput file input lives inside #welcomeOverlay.
 *     After the suite boots the overlay is hidden, but Playwright's setInputFiles
 *     works on hidden elements, so we target it directly by selector and
 *     trigger the PyScript change handler without needing the overlay visible.
 *   - Manifest bytes are serialised as JSON; yaml.safe_load on the Python
 *     side accepts JSON because YAML is a superset of JSON.
 *   - We wait for #statusBox to reflect the import result before asserting.
 */

import { test, expect, Page } from '@playwright/test';
import { goToWizardStep } from './page-objects/app-startup';
import { createSharedAppSuite } from './fixtures/shared-app';

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
    const statusBox = page.locator('#statusBox');
    await expect(statusBox).toHaveClass(/status (success|error)/, { timeout: 30_000 });
    return (await statusBox.textContent()) ?? '';
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('Workflow import — standalone workflow_state.yml', () => {
    const suite = createSharedAppSuite(test, { resetMode: 'reload' });

    test('success status shown after importing a minimal valid manifest', async () => {
        const page = suite.getPage();

        const statusText = await uploadWorkflowYml(page, manifestBytes(minimalManifest()));

        expect(statusText).toContain('Workflow defaults imported successfully.');
        await expect(page.locator('#statusBox')).toHaveClass(/success/);
    });

    test('planning period rows restored from forms.planning_periods', async () => {
        const page = suite.getPage();

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

    test('hidden modelYears and planningYears inputs reflect imported planning periods', async () => {
        const page = suite.getPage();

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

    test('error status shown for an invalid manifest (wrong schema)', async () => {
        const page = suite.getPage();

        const manifest = minimalManifest({ schema: 'not-a-valid-schema' });
        const statusText = await uploadWorkflowYml(page, manifestBytes(manifest));

        expect(statusText).toContain('Workflow import error');
        await expect(page.locator('#statusBox')).toHaveClass(/error/);
    });

    test('error status shown when manifest requires supplemental files', async () => {
        // A standalone .yml that lists required_supplemental_files must be
        // rejected — the user must upload the full ZIP instead.
        const page = suite.getPage();

        const manifest = minimalManifest({
            required_supplemental_files: ['resource_groups_unspecified_default/groups.json'],
        });
        const statusText = await uploadWorkflowYml(page, manifestBytes(manifest));

        expect(statusText).toContain('Workflow import error');
        await expect(page.locator('#statusBox')).toHaveClass(/error/);
    });

    test('error status shown for invalid (non-YAML) file content', async () => {
        const page = suite.getPage();

        // Raw bytes that are neither valid UTF-8 nor valid YAML.
        const garbage = Buffer.from([0xff, 0xfe, 0x00, 0x80]);
        const statusText = await uploadWorkflowYml(page, garbage);

        expect(statusText).toContain('Workflow import error');
        await expect(page.locator('#statusBox')).toHaveClass(/error/);
    });

    test('welcome overlay can be re-shown and the import input is then visible', async () => {
        // Verifies the overlay/input relationship: the app boots with the
        // overlay dismissed, and re-exposing it makes the file input visible.
        const page = suite.getPage();

        await page.evaluate(() => {
            document.getElementById('welcomeOverlay')?.classList.remove('hidden');
        });

        await expect(page.locator('#welcomeOverlay')).not.toHaveClass(/hidden/);
        await expect(page.locator('#uploadWorkflowInput')).toBeVisible();

        // Dismiss via the close button (same as the user would).
        await page.locator('#welcomeOverlay').getByLabel('Close welcome dialog').click();
        await expect(page.locator('#welcomeOverlay')).toHaveClass(/hidden/);
    });

    test('welcome dialog presents workflow import as the primary entry point with loading feedback', async () => {
        const page = suite.getPage();

        await page.evaluate(() => {
            document.getElementById('welcomeOverlay')?.classList.remove('hidden');
        });
        const card = page.locator('.workflow-import-card');
        await expect(card).toBeVisible();
        await expect(card).toContainText('Continue an Existing Workflow');
        await expect(page.locator('.development-warning')).toBeVisible();
        await expect(page.locator('#workflowImportProgress')).toHaveAttribute('role', 'status');
        await expect(page.locator('#workflowImportProgressMessage')).toHaveText('Loading workflow state...');
        await expect(page.locator('#workflowImportProgress')).not.toHaveClass(/visible/);
    });

    test('ATB picker forms rebuild the cascade and redraw the comparison charts', async () => {
        // Regression: imported picker values used to be written into selects
        // whose dependent option lists were still the startup defaults, so a
        // detail such as "F-Frame CC" had no matching option and reset to "".
        const page = suite.getPage();

        const manifest = minimalManifest({
            forms: {
                atbYearSelect: '2023',
                atbTechSelect: 'NaturalGas',
                atbTechDetailSelect: 'F-Frame CC',
                atbCostCaseSelect: 'Advanced',
            },
        });

        const statusText = await uploadWorkflowYml(page, manifestBytes(manifest));
        expect(statusText).toContain('Workflow defaults imported successfully.');

        // The detail option was rebuilt for NaturalGas before selection.
        await expect(
            page.locator('#atbTechDetailSelect option[value="F-Frame CC"]')
        ).toHaveCount(1);
        await expect(page.locator('#atbYearSelect')).toHaveValue('2023');
        await expect(page.locator('#atbTechSelect')).toHaveValue('NaturalGas');
        await expect(page.locator('#atbTechDetailSelect')).toHaveValue(
            'F-Frame CC'
        );
        await expect(page.locator('#atbCostCaseSelect')).toHaveValue('Advanced');

        // The import redraws the charts even while Step 4 is still hidden, so
        // the caption reflects the restored picker without any user action.
        await expect(page.locator('#atbCharts svg').first()).toHaveAttribute(
            'aria-label',
            /Selected: F-Frame CC; solid blue/
        );

        await goToWizardStep(page, 4);

        const capex = page
            .locator('#atbCharts .atb-chart-panel')
            .filter({ has: page.getByRole('heading', { name: /^CAPEX/ }) });
        const heatRate = page
            .locator('#atbCharts .atb-chart-panel')
            .filter({ has: page.getByRole('heading', { name: /^Heat rate/ }) });

        // 2023 ATB data is reported in 2021 dollars.
        await expect(capex.getByRole('heading')).toContainText('2021 $/kW');

        // One solid blue line for the selected detail in each chart, and a
        // dashed gray comparison line for the other NaturalGas detail.
        await expect(page.locator('#atbCharts svg')).toHaveCount(2);
        await expect(
            capex.locator('polyline[stroke="#1a56c4"]:not([stroke-dasharray])')
        ).toHaveCount(1);
        await expect(
            heatRate.locator('polyline[stroke="#1a56c4"]:not([stroke-dasharray])')
        ).toHaveCount(1);
        await expect(
            heatRate.locator('polyline[stroke="#9ca3af"][stroke-dasharray]')
        ).not.toHaveCount(0);
    });
});
