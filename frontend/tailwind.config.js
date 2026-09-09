/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // The Global Lab palette, from the auth mockup. Named semantically
        // rather than as a numeric ramp — these are specific brand values,
        // not a generated scale, and `bg-brand` reads better than `bg-brand-600`.
        brand: {
          DEFAULT: '#A9282C',
          hover: '#8F2024',
          deep: '#7E1C20',
          rose: '#D99A9E',
          disabled: '#D69598',
        },
        ink: '#101010',
        page: '#F2EFEF',
        muted: {
          DEFAULT: '#4A4A4A',
          soft: '#8B8585',
          faint: '#B4ACAC',
        },
        line: {
          DEFAULT: '#D5D0D0',
          soft: '#E6DFDF',
        },
        success: {
          DEFAULT: '#1E6B45',
          bg: '#F1F8F4',
          dot: '#7ED4A2',
        },
        danger: {
          DEFAULT: '#7E1C20',
          bg: '#FBF1F1',
        },

        // Repointed from sky blue to brand crimson. The existing components
        // use primary-500/600 only for accents (icons, selected borders, the
        // scrollbar thumb), so they move to crimson without edits — the
        // direction the app-wide retheme is heading anyway.
        primary: {
          50: '#F9EDEE',
          500: '#A9282C',
          600: '#8F2024',
          700: '#7E1C20',
        },

        // DEPRECATED — the old slate scale. The app itself is light now; the
        // only remaining references are the unused legacy components
        // (PersonaSelector, Controls, TranscriptionDisplay). Delete together
        // with them.
        dark: {
          900: '#0f172a',
          800: '#1e293b',
        }
      },
      fontFamily: {
        // Additive on purpose. index.css applies font-sans to <body>, so
        // overriding `sans` here would restyle every existing screen. The
        // auth routes opt in explicitly; the retheme can promote Poppins to
        // `sans` later.
        poppins: ['Poppins', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        slab: ['"Roboto Slab"', 'ui-serif', 'Georgia', 'serif'],
        plex: ['"IBM Plex Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      animation: {
        'pulse-glow': 'pulse-glow 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
      keyframes: {
        'pulse-glow': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.5' },
        }
      }
    },
  },
  plugins: [],
}
