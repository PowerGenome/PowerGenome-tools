import * as fs from 'fs';
import * as path from 'path';
import { test, expect } from '@playwright/test';
import { goToWizardStep, loadApp } from './page-objects/app-startup';

/**
 * Reads fuel_prices.csv from the data directory and returns the latest
 * data_year value as a string (e.g. "2026").
 */
function getLatestFuelDataYear(): string {
    const csvPath = path.resolve(__dirname, '../../data/fuel_prices.csv');
    const content = fs.readFileSync(csvPath, 'utf-8');
    const lines = content.trim().split('\n');
    const headers = lines[0].split(',').map((h) => h.trim());
    const yearIdx = headers.indexOf('data_year');
    if (yearIdx === -1) throw new Error('data_year column not found in fuel_prices.csv');

    const years = lines
        .slice(1)
        .map((line) => parseInt(line.split(',')[yearIdx]?.trim(), 10))
        .filter((y) => !isNaN(y));

    return String(Math.max(...years));
}

test.describe('Fuels — default data year', () => {
    test('fuelDataYear select defaults to the latest year in fuel_prices.csv', async ({ page }) => {
        const expectedYear = getLatestFuelDataYear();

        await loadApp(page, 60000);
        await goToWizardStep(page, 5);

        const fuelYearSelect = page.locator('#fuelDataYear');

        // Wait for the select to be populated (options loaded from fuel_prices.csv)
        await expect(fuelYearSelect.locator('option')).not.toHaveCount(0, { timeout: 30000 });

        const selectedValue = await fuelYearSelect.evaluate(
            (el) => (el as HTMLSelectElement).value
        );

        expect(selectedValue).toBe(expectedYear);
    });
});
