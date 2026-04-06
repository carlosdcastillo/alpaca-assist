/**
 * Jest Setup File - Runs before each test file
 * CRITICAL: Uses require() not import - Jest runs in CommonJS mode by default
 */

// Mock requestAnimationFrame before any imports
global.requestAnimationFrame = (callback) => setTimeout(callback, 0);
global.cancelAnimationFrame = (id) => clearTimeout(id);

// jsdom doesn't implement ResizeObserver — provide a no-op stub
global.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};

// jsdom doesn't implement scrollIntoView
Element.prototype.scrollIntoView = jest.fn();

// Optional: Install jest-dom matchers (using require, not import)
try {
  require("@testing-library/jest-dom");
} catch (e) {
  console.warn("@testing-library/jest-dom not installed, skipping");
}

// Suppress console noise during tests unless debugging
const originalConsoleLog = console.log;
global.console = {
  ...console,
  log: (...args) => {
    if (process.env.DEBUG_TESTS) {
      originalConsoleLog(...args);
    }
  },
  debug: jest.fn(),
  info: jest.fn(),
  error: jest.fn(), // Mock for assertions like expect(console.error).toHaveBeenCalled()
  warn: jest.fn(), // Mock for consistency
};
