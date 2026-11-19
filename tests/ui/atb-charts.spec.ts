import { test, expect, Page } from '@playwright/test';
import { createSharedAppSuite } from './fixtures/shared-app';
import { NewResourcesTab } from './page-objects/new-resources-tab';
import { goToWizardStep } from './page-objects/app-startup';

// Exercise the served ATB parquet through the real PyScript app, not injected
// chart markup or synthetic picker options. Heat-rate values below are source
// observations; CAPEX labels are converted from the source $/MW to $/kW for
// display.
async function selectThermal(page: Page) {
    await new NewResourcesTab(page).goto();
    await page.locator('#atbYearSelect').selectOption('2023');
    await page.locator('#atbTechSelect').selectOption('NaturalGas');
    await page.locator('#atbTechDetailSelect').selectOption('F-Frame CC');
    await page.locator('#atbCostCaseSelect').selectOption('Moderate');
}

async function expectSelection(page: Page, tech: string, detail: string, year: string, costCase: string) {
    await expect(page.locator('#atbYearSelect')).toHaveValue(year);
    await expect(page.locator('#atbTechSelect')).toHaveValue(tech);
    await expect(page.locator('#atbTechDetailSelect')).toHaveValue(detail);
    await expect(page.locator('#atbCostCaseSelect')).toHaveValue(costCase);
    const charts = page.locator('#atbCharts');
    await expect(charts).toHaveAttribute('aria-busy', 'false');
    await expect(charts.locator('h3')).toHaveText('ATB data');
    await expect(charts).not.toContainText('Defaults only; resource overrides');
    await expect(charts.locator('.atb-chart-legend')).toHaveCount(0);
    await expect(charts.getByRole('list', { name: 'Tech details' })).toHaveCount(0);
    await expect(charts.locator('.atb-chart-panel').first().locator('polyline:not([stroke-dasharray])')).toHaveAttribute('stroke', '#1a56c4');
    await expect(charts.locator('svg').first()).toHaveAttribute('aria-label', new RegExp(`Selected: ${detail.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')};`));
}

test.describe('New Resources — ATB data charts', () => {
    const suite = createSharedAppSuite(test, {
        reset: async (page) => {
            await page.setViewportSize({ width: 1280, height: 720 });
            await new NewResourcesTab(page).resetForTest();
        }
    });
    const page = () => suite.getPage();
    const capex = () => page().locator('#atbCharts .atb-chart-panel').filter({ has: page().getByRole('heading', { name: /^CAPEX/ }) });
    const heatRate = () => page().locator('#atbCharts .atb-chart-panel').filter({ has: page().getByRole('heading', { name: /^Heat rate/ }) });
    const selectedLine = () => heatRate().locator('polyline:not([stroke-dasharray])');

    test('technology, detail, case and publication year keep charts synchronized', async () => {
        await selectThermal(page());
        await expectSelection(page(), 'NaturalGas', 'F-Frame CC', '2023', 'Moderate');
        await expect(capex().getByRole('img')).toBeVisible();
        await expect(selectedLine()).toHaveAttribute('stroke', '#1a56c4');
        await expect(heatRate().locator('circle > title').filter({ hasText: /^F-Frame CC: 6\.238$/ }).first()).toHaveCount(1);
        await expect(heatRate().locator('circle > title').filter({ hasText: /^F-Frame CC: 6\.159$/ }).first()).toHaveCount(1);
        await expect(heatRate().locator('polyline[stroke-dasharray]').first()).toHaveAttribute('stroke', '#9ca3af');

        await page().locator('#atbTechDetailSelect').selectOption('H-Frame CC');
        await expectSelection(page(), 'NaturalGas', 'H-Frame CC', '2023', 'Moderate');
        await expect(selectedLine()).toHaveAttribute('stroke', '#1a56c4');
        await expect(heatRate().locator('circle > title').filter({ hasText: /^H-Frame CC: 6\.122$/ }).first()).toHaveCount(1);
        await expect(heatRate().locator('circle > title').filter({ hasText: /^F-Frame CC:/ }).first()).toHaveCount(1);

        await page().locator('#atbCostCaseSelect').selectOption('Advanced');
        await expectSelection(page(), 'NaturalGas', 'H-Frame CC', '2023', 'Advanced');
        await expect(heatRate().locator('circle > title').filter({ hasText: /^H-Frame CC: 6\.048$/ }).first()).toHaveCount(1);

        await page().locator('#atbYearSelect').selectOption('2024');
        const detail = await page().locator('#atbTechDetailSelect').inputValue();
        await expectSelection(page(), 'NaturalGas', detail, '2024', 'Advanced');
        await expect(heatRate().locator('circle > title').filter({ hasText: /^F-Frame CC:/ })).toHaveCount(0);
        await expect(capex().getByRole('img')).toBeVisible();
    });

    test('clicking an existing resource updates chart selection and source observations', async () => {
        const resources = new NewResourcesTab(page());
        await resources.goto();
        // Resource clicks preserve the selected publication year. Use the year
        // containing the defaults' detail names, but start on a different tech.
        await page().locator('#atbYearSelect').selectOption('2024');
        await page().locator('#atbTechSelect').selectOption('UtilityPV');
        await page().locator('#atbCostCaseSelect').selectOption('Advanced');
        await resources.item(0).click();
        await expectSelection(page(), 'NaturalGas', '2-on-1 Combined Cycle (H-Frame)', '2024', 'Moderate');
        await expect(capex().locator('circle > title').filter({ hasText: /^2-on-1 Combined Cycle \(H-Frame\): 1,316$/ }).first()).toHaveCount(1);
        await expect(heatRate().getByRole('img')).toBeVisible();
        await expect(heatRate().getByRole('heading')).toContainText('MMBtu/MWh');
        await expect(selectedLine()).toHaveAttribute('stroke', '#1a56c4');
        // Match the observation to the 2030 axis tick, not just any point
        // sharing this heat rate elsewhere in the projection.
        const projection2030 = heatRate().locator('svg text').filter({ hasText: /^2030$/ });
        await expect(projection2030).toHaveCount(1);
        const projectionX = await projection2030.getAttribute('x');
        expect(projectionX).not.toBeNull();
        await expect(heatRate().locator(`circle[cx="${projectionX}"] > title`).filter({
            hasText: /^2-on-1 Combined Cycle \(H-Frame\): 6\.122$/,
        })).toHaveCount(1);

        await resources.item(2).click();
        await expectSelection(page(), 'LandbasedWind', 'Class3', '2024', 'Moderate');
        await expect(capex().getByRole('img')).toBeVisible();
        await expect(heatRate().getByRole('img')).toHaveCount(0);
        await expect(heatRate()).toContainText('No heat-rate values reported');
    });

    test('charts explain axes without verbose root or line hover titles', async () => {
        await selectThermal(page());
        await expect(capex().getByRole('heading')).toContainText('$/kW');
        await expect(heatRate().getByRole('heading')).toContainText('MMBtu/MWh');
        for (const panel of [capex(), heatRate()]) {
            const svg = panel.getByRole('img');
            await expect(svg.locator(':scope > title, polyline title, path title')).toHaveCount(0);
            await expect(svg.locator(':scope > desc')).toHaveText((await svg.getAttribute('aria-label'))!);
            const titles = svg.locator('title');
            await expect(titles.first()).toBeAttached();
            expect(await titles.count()).toBe(await svg.locator('circle > title').count());
            for (const title of await titles.allTextContents()) {
                // Exactly one detail/value pair, without projection year,
                // units, selected status, or explanatory prose.
                expect(title).toMatch(/^[^:]+: \d[\d,]*(?:\.\d+)?(?:e[+-]\d+)?$/);
            }
        }
    });

    test('desktop New Resources retains the shared 760px centered column', async () => {
        await page().setViewportSize({ width: 1440, height: 900 });
        await goToWizardStep(page(), 2);
        const modelSetup = page().locator('#step-2 .centered-step');
        await expect(modelSetup).toBeVisible();
        const reference = await modelSetup.boundingBox();
        expect(reference).not.toBeNull();

        await selectThermal(page());
        const resources = page().locator('#step-4 .centered-step');
        await expect(resources).toHaveCSS('max-width', '760px');
        const column = await resources.boundingBox();
        expect(column).not.toBeNull();
        expect(column!.width).toBeCloseTo(reference!.width, 0);
        expect(column!.x).toBeCloseTo(reference!.x, 0);
    });

    for (const technology of ['LandbasedWind', 'UtilityPV']) {
        test(`${technology} clears thermal curves and explains missing heat rates`, async () => {
            await selectThermal(page());
            await expect(heatRate().getByRole('img')).toBeVisible();
            await page().locator('#atbTechSelect').selectOption(technology);
            const detail = await page().locator('#atbTechDetailSelect').inputValue();
            await expectSelection(page(), technology, detail, '2023', 'Moderate');
            await expect(capex().getByRole('img')).toBeVisible();
            await expect(heatRate().getByRole('img')).toHaveCount(0);
            await expect(heatRate().locator('polyline, circle')).toHaveCount(0);
            await expect(heatRate()).toContainText('Non-combustion technologies such as wind and solar do not have a heat rate.');
        });
    }

    for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
        test(`charts and picker fit locally within New Resources at ${viewport.width}px`, async () => {
            await page().setViewportSize(viewport);
            await selectThermal(page());
            await expect(capex().getByRole('img')).toBeVisible();
            await expect(heatRate().getByRole('img')).toBeVisible();
            // Deliberately scoped to New Resources, not unrelated page/sidebar
            // document overflow. Check both scroll overflow and child bounds.
            const overflow = await page().locator('#step-4').evaluate((step) => {
                const section = step.getBoundingClientRect();
                return Array.from(step.querySelectorAll<HTMLElement>(
                    '.atb-explorer, .atb-controls, #atbCharts, .atb-chart-panel, .atb-controls select, #atbCharts svg'
                )).filter((element) => {
                    const rect = element.getBoundingClientRect();
                    return element.scrollWidth > element.clientWidth + 1
                        || rect.left < section.left - 1 || rect.right > section.right + 1;
                }).map((element) => element.id || element.className || element.tagName);
            });
            expect(overflow).toEqual([]);
            for (const id of ['atbYearSelect', 'atbTechSelect', 'atbTechDetailSelect', 'atbCostCaseSelect']) {
                await expect(page().locator(`#${id}`)).toBeVisible();
                await expect(page().locator(`#${id}`)).toBeEnabled();
            }
            await page().locator('#atbCostCaseSelect').selectOption('Advanced');
            await expectSelection(page(), 'NaturalGas', 'F-Frame CC', '2023', 'Advanced');
            const controls = await page().locator('#step-4 .atb-controls').boundingBox();
            const charts = await page().locator('#atbCharts').boundingBox();
            expect(controls).not.toBeNull();
            expect(charts).not.toBeNull();
            if (viewport.width < 768) {
                expect(charts!.y).toBeGreaterThanOrEqual(controls!.y + controls!.height);
            } else {
                expect(charts!.x).toBeGreaterThanOrEqual(controls!.x + controls!.width);
                // The compressed CAPEX + heat-rate pair must not run taller
                // than the picker column on desktop.
                expect(charts!.height).toBeLessThanOrEqual(controls!.height + 1);
            }
        });
    }
});

// Regression: the charts first render while Step 4 is hidden, so
// container.clientWidth is 0 and render_atb_charts() falls back to a
// viewport-based estimate. goToStep(4) must redraw synchronously after the
// pane becomes active so the viewBox reflects the measured container width.
// No page.setViewportSize() here: a resize event would redraw the charts and
// mask a missing entry-time render.
test.describe('New Resources — first entry measures the pane', () => {
    const suite = createSharedAppSuite(test, { resetMode: 'reload' });

    test('entering the step redraws charts at the measured container width', async () => {
        const page = suite.getPage();
        await expect(page.locator('#step-1')).toHaveClass(/active/);

        const before = await page.evaluate(() => {
            const container = document.getElementById('atbCharts');
            const svg = container?.querySelector('svg');
            return {
                clientWidth: container?.clientWidth ?? -1,
                viewBox: svg?.getAttribute('viewBox') ?? null,
                innerWidth: window.innerWidth,
            };
        });

        // The hidden pane has no measurable width, so the startup render used
        // the viewport-based estimate.
        expect(before.clientWidth).toBe(0);
        expect(before.viewBox).not.toBeNull();
        const beforeWidth = Number(before.viewBox!.trim().split(/\s+/)[2]);
        expect(Number.isFinite(beforeWidth)).toBe(true);
        expect(beforeWidth).toBeGreaterThan(0);

        await goToWizardStep(page, 4);

        const after = await page.evaluate(() => {
            const container = document.getElementById('atbCharts')!;
            const viewBoxes = Array.from(container.querySelectorAll('svg')).map(
                (svg) =>
                    (svg.getAttribute('viewBox') ?? '')
                        .trim()
                        .split(/\s+/)
                        .map(Number)
            );
            return {
                clientWidth: container.clientWidth,
                innerWidth: window.innerWidth,
                viewBoxes,
            };
        });

        expect(after.clientWidth).toBeGreaterThan(0);
        expect(after.viewBoxes.length).toBeGreaterThan(0);
        const expectedWidth =
            after.clientWidth - (after.innerWidth > 850 ? 24 : 0);

        for (const [x, y, width, height] of after.viewBoxes) {
            expect([x, y]).toEqual([0, 0]);
            expect(width).toBe(expectedWidth);
            expect(height).toBe(108);
        }

        // The entry render must not reuse the estimate from the hidden pane.
        // (At the config viewport the estimate is 440px; the measured width is
        // smaller, so this fails on the old estimate-only code.)
        expect(beforeWidth).not.toBe(expectedWidth);
    });
});
