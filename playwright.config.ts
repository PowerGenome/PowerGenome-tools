import { defineConfig } from '@playwright/test';

export default defineConfig({
    testDir: './tests/ui',

    // Run tests in parallel
    fullyParallel: true,

    // Fail the build on CI if you accidentally left test.only in the source code
    forbidOnly: !!process.env.CI,

    // Retry on CI only
    retries: process.env.CI ? 2 : 0,

    // Opt out of parallel tests on CI
    workers: process.env.CI ? 1 : undefined,

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
                ...require('@playwright/test').devices['Desktop Chrome'],
                // Increase timeout for PyScript loading
                actionTimeout: 30000,
                navigationTimeout: 30000,
            },
        },
    ],

    // Start web server before running tests
    webServer: {
        command: 'cd web && python -m http.server 8001',
        url: 'http://127.0.0.1:8001',
        reuseExistingServer: true,
        timeout: 120 * 1000,
        stdout: 'ignore',
        stderr: 'pipe',
    },
});
