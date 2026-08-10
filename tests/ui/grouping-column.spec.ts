/**
 * Tests for the Grouping Column selector (#groupingColumn) in the Regions step.
 *
 * Changes introduced with the hierarchy update:
 *  - Default selected option changed from "transgrp" to "nercr-latest"
 *  - New option: "NERC - latest (nercr-latest)"
 *  - Renamed option: "NERC - legacy (nercr)" (was "NERC Region (nercr)")
 */

import { test, expect } from '@playwright/test';
import { createSharedAppSuite } from './fixtures/shared-app';

test.describe('Grouping Column selector', () => {
    const suite = createSharedAppSuite(test);

    const groupingSelect = () => suite.getPage().locator('#groupingColumn');

    // ------------------------------------------------------------------
    // Default selected value
    // ------------------------------------------------------------------

    test('nercr-latest is selected by default', async () => {
        const selectedValue = await groupingSelect().inputValue();
        expect(selectedValue).toBe('nercr-latest');
    });

    test('transgrp is NOT selected by default', async () => {
        const selectedValue = await groupingSelect().inputValue();
        expect(selectedValue).not.toBe('transgrp');
    });

    // ------------------------------------------------------------------
    // Option presence and labels
    // ------------------------------------------------------------------

    test('nercr-latest option exists with correct label', async () => {
        const option = groupingSelect().locator('option[value="nercr-latest"]');
        await expect(option).toBeAttached();
        await expect(option).toHaveText('NERC - latest (nercr-latest)');
    });

    test('nercr option exists with legacy label', async () => {
        const option = groupingSelect().locator('option[value="nercr"]');
        await expect(option).toBeAttached();
        await expect(option).toHaveText('NERC - legacy (nercr)');
    });

    test('transgrp option still exists', async () => {
        const option = groupingSelect().locator('option[value="transgrp"]');
        await expect(option).toBeAttached();
    });

    // ------------------------------------------------------------------
    // Switching to each option works without errors
    // ------------------------------------------------------------------

    test('can switch grouping to transgrp without error', async () => {
        await groupingSelect().selectOption('transgrp');
        await expect(groupingSelect()).toHaveValue('transgrp');
    });

    test('can switch grouping to nercr (legacy) without error', async () => {
        await groupingSelect().selectOption('nercr');
        await expect(groupingSelect()).toHaveValue('nercr');
    });

    test('can switch grouping back to nercr-latest without error', async () => {
        await groupingSelect().selectOption('nercr-latest');
        await expect(groupingSelect()).toHaveValue('nercr-latest');
    });
});
