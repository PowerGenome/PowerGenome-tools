# UI Tests for PowerGenome Tools Web App

This directory contains browser-based UI tests using Playwright to verify the web application behavior in real browsers.

## Current Status

✅ **All 13 tests passing** - Complete browser-based test coverage for the Model Setup tab (Step 2) planning period functionality

## Setup

1. Install Node.js dependencies:

   ```bash
   npm install
   ```

2. Install Playwright browsers:

   ```bash
   npx playwright install
   ```

## Running Tests

### All UI tests

```bash
npm run test:ui
```

### Run with browser visible (headed mode)

```bash
npm run test:ui:headed
```

### Debug tests (step through with browser dev tools)

```bash
npm run test:ui:debug
```

### View test report

```bash
npm run test:ui:report
```

### Run specific test file

```bash
npx playwright test smoke.spec.ts
npx playwright test model-setup.spec.ts
```

## Test Structure

- **`smoke.spec.ts`** - Basic functionality tests to ensure the app loads and PyScript initializes
- **`model-setup.spec.ts`** - Comprehensive tests for Step 2 (Model Setup) planning period functionality
- **`fixtures/shared-app.ts`** - Shared app suite that boots PyScript once per spec file
- **`page-objects/`** - Page object models that encapsulate UI interactions

## Shared app suites

Booting PyScript/Pyodide takes several seconds per page, so new specs should
use `createSharedAppSuite` instead of calling `loadApp()` in every test:

```ts
import { test, expect } from '@playwright/test';
import { createSharedAppSuite } from './fixtures/shared-app';

test.describe('My feature', () => {
    // Runs before every test, on the shared page.
    const suite = createSharedAppSuite(test, {
        reset: async (page) => {
            // Restore any state the previous test mutated.
        }
    });

    test('does something', async () => {
        const page = suite.getPage();
        // ...
    });
});
```

- The suite runs in serial mode and creates one `BrowserContext` per spec file.
- `reset` must put the page back into the state each test expects. When a test
  mutates state that has no cheap reset, use
  `createSharedAppSuite(test, { resetMode: 'reload' })`: a reload restarts
  PyScript/Pyodide inside the same context and reuses its HTTP cache, which is
  still much cheaper than booting a fresh context per test.
- Keep `loadApp()` only for specs that genuinely cannot share a page (for
  example a single-test file, where sharing saves nothing).

## Key Features Tested

### Model Setup Tab (Step 2)

- Default values (current year, 2030) render correctly
- Adding planning periods auto-populates derived values
- Manual editing of auto-populated fields works
- Hidden input synchronization with visible UI
- Client-side validation errors and clearing
- Row removal and add functionality

### Smoke Tests

- PyScript initialization and loading screen behavior
- Basic navigation between wizard steps
- Core UI components render correctly

## Configuration

The tests are configured in `playwright.config.ts` to:

- Start a local HTTP server serving the `web/` directory
- Run tests against `http://127.0.0.1:8001`
- Use Chrome/Chromium by default
- Capture screenshots on failure
- Generate HTML and JUnit reports
- Handle PyScript loading timeouts (30s)

## Tips for Writing Tests

1. **Wait for PyScript**: Always wait for `#loading.hidden` before interacting with Python-driven UI
2. **Use Page Objects**: Encapsulate complex interactions in the `page-objects/` classes
3. **Test Real Behavior**: These tests verify actual browser DOM behavior, unlike the Python unit tests
4. **Validation Timing**: Use `waitForValidation()` helper to let client-side validation complete
5. **Selector Strategy**: Prefer semantic selectors (roles, labels) over CSS classes when possible

## Debugging Test Failures

1. Run in headed mode to see what's happening: `npm run test:ui:headed`
2. Use debug mode to step through: `npm run test:ui:debug`
3. Check the HTML report for screenshots and traces: `npm run test:ui:report`
4. Verify the local server is working: visit `http://127.0.0.1:8001` manually
