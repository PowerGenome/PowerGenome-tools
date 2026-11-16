import { defineConfig, devices } from '@playwright/test';

// Windows CI runners expose `python` (from actions/setup-python); macOS/Linux
// commonly ship only `python3`.
const pythonCommand = process.platform === 'win32' ? 'python' : 'python3';

// Each worker boots its own Pyodide runtime with pandas/numpy/shapely, which is
// memory hungry. Two workers overlap the (serial) shared-app suites by file on
// Linux CI; Windows CI keeps one worker for headroom. RUNNER_OS is set by
// GitHub Actions, so local runs never pick up the CI value.
const ciWorkers = process.env.RUNNER_OS === 'Windows' ? 1 : 2;

export default defineConfig({
    testDir: './tests/ui',

    // Run tests in parallel
    fullyParallel: true,

    // Fail the build on CI if you accidentally left test.only in the source code
    forbidOnly: !!process.env.CI,

    // Retry on CI only
    retries: process.env.CI ? 1 : 0,

    // Limit parallelism on CI; local runs stay fully parallel.
    workers: process.env.CI ? ciWorkers : undefined,

    // Reporter configuration
    reporter: [
        ['html'],
        ['junit', { outputFile: 'test-results/results.xml' }]
    ],

    use: {
        baseURL: 'http://127.0.0.1:8001',

        // Collect trace when retrying the failed test
        trace: 'on-first-retry',

        // Take screenshot on failure
        screenshot: 'only-on-failure',
    },

    // Configure projects for different browsers
    projects: [
        {
            name: 'chromium',
            use: {
                ...devices['Desktop Chrome'],
                // Increase timeout for PyScript loading
                actionTimeout: 30000,
                navigationTimeout: 30000,
            },
        },
    ],

    // Start web server before running tests
    webServer: {
        command: `cd web && ${pythonCommand} -m http.server 8001`,
        url: 'http://127.0.0.1:8001',
        reuseExistingServer: true,
        timeout: 120 * 1000,
        stdout: 'ignore',
        stderr: 'pipe',
    },
});
