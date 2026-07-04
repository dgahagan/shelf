/** @type {import('tailwindcss').Config} */
// Replaces the former cdn.tailwindcss.com runtime config (see docs/plans/CSP_BUNDLING.md).
// Rebuild static/css/app.css with `make css` after changing templates or this file.
module.exports = {
  darkMode: 'class',
  content: [
    './app/templates/**/*.html',
    './static/js/**/*.js',
  ],
  theme: {
    extend: {
      colors: {
        shelf: {
          bg: '#0f1117',
          card: '#1a1d27',
          hover: '#242836',
          accent: '#6366f1',
          accent2: '#818cf8',
          text: '#e2e8f0',
          muted: '#94a3b8',
          border: '#2d3148',
          success: '#22c55e',
          warning: '#eab308',
          error: '#ef4444',
        },
      },
    },
  },
  plugins: [],
};
