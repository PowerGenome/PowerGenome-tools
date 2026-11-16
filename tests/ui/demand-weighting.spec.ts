import { test, expect, Page } from '@playwright/test';
import { goToWizardStep } from './page-objects/app-startup';
import { createSharedAppSuite } from './fixtures/shared-app';

type RegionAggregations = Record<string, string[]>;

function parseRegionAggregations(yamlText: string): RegionAggregations {
    const lines = yamlText.replace(/\r\n/g, '\n').split('\n');
    const regionAggregations: RegionAggregations = {};
    let inRegionAggregations = false;
    let currentRegion: string | null = null;

    for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) {
            continue;
        }

        if (!inRegionAggregations) {
            if (trimmed === 'region_aggregations:') {
                inRegionAggregations = true;
            }
            continue;
        }

        if (!line.startsWith('  ')) {
            break;
        }

        const regionMatch = line.match(/^  ([^:\n]+):\s*$/);
        if (regionMatch) {
            currentRegion = regionMatch[1];
            regionAggregations[currentRegion] = [];
            continue;
        }

        const baMatch = line.match(/^\s{2,4}-\s+(.+?)\s*$/);
        if (baMatch && currentRegion) {
            regionAggregations[currentRegion].push(baMatch[1]);
        }
    }

    if (Object.keys(regionAggregations).length === 0) {
        throw new Error('Failed to parse region_aggregations from YAML output.');
    }

    return regionAggregations;
}

async function readTransgrpByBa(page: Page): Promise<Record<string, string>> {
    return page.evaluate(async () => {
        const response = await fetch('/data/hierarchy.csv');
        const csvText = await response.text();
        const [headerLine, ...rows] = csvText.trim().split(/\r?\n/);
        const headers = headerLine.split(',');
        const baIndex = headers.indexOf('ba');
        const transgrpIndex = headers.indexOf('transgrp');
        const mapping: Record<string, string> = {};

        for (const row of rows) {
            const columns = row.split(',');
            mapping[columns[baIndex]] = columns[transgrpIndex];
        }

        return mapping;
    });
}

/**
 * Restore the Regions step to the defaults asserted by this file.
 *
 * The dropdown tests share one booted app and only mutate the demand weighting
 * dropdown and the auto-optimize checkbox, so resetting those two (plus the BA
 * selection, for any future test that selects regions) is cheaper and just as
 * correct as reloading the page.
 */
async function resetRegionsStep(page: Page) {
    await goToWizardStep(page, 1);

    const autoOptimize = page.locator('#autoOptimize');
    if (await autoOptimize.isChecked()) {
        // Fires the change handler that re-shows the demand weighting section.
        await autoOptimize.uncheck();
    }

    const demandWeight = page.locator('#demandWeightMethod');
    if ((await demandWeight.inputValue()) !== 'none') {
        await demandWeight.selectOption('none');
    }

    const selectedCount = page.locator('#selectedCount');
    if (((await selectedCount.textContent()) ?? '').trim() !== '0') {
        await page.locator('#clearSelectionBtn').click();
        await expect(selectedCount).toHaveText('0');
    }
}

test.describe('Demand Weighting Dropdown (Step 1)', () => {
    const suite = createSharedAppSuite(test, { reset: resetRegionsStep });

    test('demand weight dropdown is visible in clustering mode', async () => {
        const page = suite.getPage();

        // Step 1 is shown by default
        const dropdown = page.locator('#demandWeightMethod');
        await expect(dropdown).toBeVisible();
    });

    test('demand weight dropdown defaults to none', async () => {
        const page = suite.getPage();

        const dropdown = page.locator('#demandWeightMethod');
        await expect(dropdown).toHaveValue('none');
    });

    test('demand weight dropdown has all three options', async () => {
        const page = suite.getPage();

        const dropdown = page.locator('#demandWeightMethod');
        const options = dropdown.locator('option');
        await expect(options).toHaveCount(3);

        const values = await options.evaluateAll((opts: HTMLOptionElement[]) =>
            opts.map(o => o.value)
        );
        expect(values).toContain('none');
        expect(values).toContain('demand-sqrt');
        expect(values).toContain('demand-log');
    });

    test('can change demand weight to sqrt method', async () => {
        const page = suite.getPage();

        const dropdown = page.locator('#demandWeightMethod');
        await dropdown.selectOption('demand-sqrt');
        await expect(dropdown).toHaveValue('demand-sqrt');
    });

    test('can change demand weight to log method', async () => {
        const page = suite.getPage();

        const dropdown = page.locator('#demandWeightMethod');
        await dropdown.selectOption('demand-log');
        await expect(dropdown).toHaveValue('demand-log');
    });

    test('demand weight section is hidden when auto-optimize is enabled', async () => {
        const page = suite.getPage();

        const autoOptimizeCheckbox = page.locator('#autoOptimize');
        const demandSection = page.locator('#demandWeightSection');

        // Initially visible
        await expect(demandSection).toBeVisible();

        // Enable auto-optimize
        await autoOptimizeCheckbox.check();

        // Demand weight section should be hidden
        await expect(demandSection).toBeHidden();
    });

    test('demand weight section reappears when auto-optimize is disabled', async () => {
        const page = suite.getPage();

        const autoOptimizeCheckbox = page.locator('#autoOptimize');
        const demandSection = page.locator('#demandWeightSection');

        // Enable then disable auto-optimize
        await autoOptimizeCheckbox.check();
        await expect(demandSection).toBeHidden();

        await autoOptimizeCheckbox.uncheck();
        await expect(demandSection).toBeVisible();
    });
});

test.describe('Demand-weighted clustering regressions', () => {
    // Separate suite: the clustering run dirties the region selections and
    // clustering inputs, so it gets its own clean app instance.
    const suite = createSharedAppSuite(test, { startupTimeout: 90000 });

    test('real app keeps p8 clustered only with NorthernGrid_South members', async () => {
        test.setTimeout(180000);

        const page = suite.getPage();
        const transgrpByBa = await readTransgrpByBa(page);
        expect(transgrpByBa.p8).toBe('NorthernGrid_South');

        await page.locator('#selectAllBtn').click();
        await expect(page.locator('#selectedCount')).toHaveText('134');

        await page.locator('#groupingColumn').selectOption('transgrp');
        await page.locator('#esrCompatibleClustering').uncheck();
        await page.locator('#clusteringMethod').selectOption('hierarchical-average');
        await page.locator('#targetRegions').fill('26');
        await page.locator('#demandWeightMethod').selectOption('demand-log');

        await page.locator('#runBtn').click();

        await expect.poll(
            async () => await page.locator('#yamlOut').inputValue(),
            { timeout: 120000 }
        ).toContain('region_aggregations:');

        await expect.poll(
            async () => await page.locator('#yamlOut').inputValue(),
            { timeout: 120000 }
        ).toContain('p8');

        const yamlOutput = await page.locator('#yamlOut').inputValue();
        const regionAggregations = parseRegionAggregations(yamlOutput);
        const p8RegionMembers = Object.values(regionAggregations).find((members) => members.includes('p8'));

        expect(p8RegionMembers).toBeTruthy();
        expect((p8RegionMembers ?? []).length).toBeGreaterThan(1);
        expect((p8RegionMembers ?? []).every((ba) => transgrpByBa[ba] === 'NorthernGrid_South')).toBe(true);
    });
});
