import type { Config } from 'tailwindcss';

/** Neutral graphite workbench tokens (same restrained language as the argus
 * cockpit): colour communicates state, not decoration. */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        bg: '#0f100e',
        surface: '#161714',
        panel: '#1d1e1a',
        'panel-2': '#22231e',
        line: '#32332d',
        'line-soft': '#282a24',
        blue: { DEFAULT: '#8fa7b8', deep: '#607d91', sky: '#b0c3cf' },
        gold: { DEFAULT: '#c7a66a', soft: '#ddc99e', deep: '#a88955' },
        ok: '#7fa386',
        warn: '#c1a363',
        err: '#c77b72',
        ink: { DEFAULT: '#efeee8', dim: '#b8b7af', faint: '#7e7d75' },
        // role hues (planner / reviewer / env_author / skill_author / manager …)
        planner: '#a69daf',
        reviewer: '#b5a57f',
        env_author: '#8fa78f',
        skill_author: '#90a8b5',
        manager: '#c7a66a',
      },
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      borderRadius: { xl: '0.625rem' },
      boxShadow: {
        glow: '0 14px 36px rgba(0,0,0,0.28)',
        card: '0 1px 0 rgba(255,255,255,0.02) inset, 0 8px 24px -12px rgba(0,0,0,0.5)',
      },
      keyframes: {
        'fade-in': { '0%': { opacity: '0', transform: 'translateY(3px)' }, '100%': { opacity: '1', transform: 'translateY(0)' } },
        breathe: { '0%,100%': { opacity: '1' }, '50%': { opacity: '0.45' } },
      },
      animation: {
        'fade-in': 'fade-in 0.24s ease-out both',
        breathe: 'breathe 2s ease-in-out infinite',
      },
    },
  },
  plugins: [],
} satisfies Config;
