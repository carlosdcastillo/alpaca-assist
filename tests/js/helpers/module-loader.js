/**
 * Module Loader Helper for Browser-Style JS Files
 *
 * These files use window.X = X or window.x = new X() patterns.
 * We need to require() them (which executes the file) then read from window.
 */

const fs = require("fs");
const path = require("path");

/**
 * Load a browser-style JS file and extract its exports
 * Handles both patterns:
 *   - window.ClassName = ClassName (class assignment)
 *   - window.instanceName = new ClassName() (instance assignment)
 * @param {string} relativePath - Path relative to project root (e.g., 'web/js/api.js')
 * @returns {Object} Object containing exported classes/functions
 */
function loadModule(relativePath) {
  // __dirname is tests/js/helpers/
  // Go up 3 levels to reach project root: helpers -> js -> tests -> root
  const fullPath = path.join(__dirname, "..", "..", "..", relativePath);

  if (!fs.existsSync(fullPath)) {
    throw new Error(`Module not found: ${fullPath}`);
  }

  // Save current window state
  const windowBefore = { ...globalThis };

  // Require the file - this executes it and populates window.*
  require(fullPath);

  // Find what was added to window
  // NOTE: Only Pattern 1 works reliably. Classes defined inside a required file
  // are scoped to the module wrapper and won't appear on globalThis unless
  // explicitly assigned to window (which Section 3 mandates).
  const exports = {};
  const content = fs.readFileSync(fullPath, "utf-8");

  // Pattern 1: window.ClassName = ClassName (class assignment)
  // This is the ONLY reliable pattern - requires explicit window assignment
  const classAssignPattern = /window\.(\w+)\s*=\s*\1/g;
  let match;
  while ((match = classAssignPattern.exec(content)) !== null) {
    const name = match[1];
    if (globalThis[name] && globalThis[name] !== windowBefore[name]) {
      exports[name] = globalThis[name];
    }
  }

  return exports;
}

/**
 * Load multiple modules in dependency order
 */
function loadModulesOrdered(paths) {
  const combined = {};

  paths.forEach((p) => {
    const exports = loadModule(p);
    Object.assign(combined, exports);

    // Also make available on global window for inter-module references
    Object.keys(exports).forEach((key) => {
      globalThis[key] = exports[key];
    });
  });

  return combined;
}

module.exports = { loadModule, loadModulesOrdered };
