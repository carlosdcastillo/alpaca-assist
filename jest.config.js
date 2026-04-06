/** @type {import('jest').Config} */
module.exports = {
  testEnvironment: "jsdom",

  roots: ["<rootDir>/tests/js"],

  testMatch: ["**/__tests__/**/*.js", "**/?(*.)+(spec|test).js"],

  setupFilesAfterEnv: ["<rootDir>/tests/js/setup.js"],

  collectCoverageFrom: [
    "web/js/**/*.js",
    "!web/js/lib/**",
    "!**/node_modules/**",
  ],

  coverageThreshold: {
    global: {
      branches: 50,
      functions: 50,
      lines: 50,
      statements: 50,
    },
  },

  clearMocks: true,
  verbose: true,
};
