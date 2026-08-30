/** @type {import('tailwindcss').Config} */
import animate from 'tailwindcss-animate';

export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{ts,tsx,js,jsx}'],
  theme: {
    extend: {
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)'
      },
      colors: {
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))'
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))'
        },
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))'
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))'
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))'
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))'
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))'
        },
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        chart: {
          1: 'hsl(var(--chart-1))',
          2: 'hsl(var(--chart-2))',
          3: 'hsl(var(--chart-3))',
          4: 'hsl(var(--chart-4))',
          5: 'hsl(var(--chart-5))'
        },
        sidebar: {
          DEFAULT: 'hsl(var(--sidebar-background))',
          foreground: 'hsl(var(--sidebar-foreground))',
          primary: 'hsl(var(--sidebar-primary))',
          'primary-foreground': 'hsl(var(--sidebar-primary-foreground))',
          accent: 'hsl(var(--sidebar-accent))',
          'accent-foreground': 'hsl(var(--sidebar-accent-foreground))',
          border: 'hsl(var(--sidebar-border))',
          ring: 'hsl(var(--sidebar-ring))'
        },
        command: '#0066FF'
      },
      keyframes: {
        'accordion-down': {
          from: {
            height: '0'
          },
          to: {
            height: 'var(--radix-accordion-content-height)'
          }
        },
        'accordion-up': {
          from: {
            height: 'var(--radix-accordion-content-height)'
          },
          to: {
            height: '0'
          }
        },
        'bounce-subtle': {
          '0%, 100%': { transform: 'translateY(0) scale(1)' },
          '50%': { transform: 'translateY(-2px) scale(1.05)' }
        },
        'command-shift': {
          '0%': { transform: 'translateX(-10px)', opacity: '0.8' },
          '50%': { transform: 'translateX(5px)' },
          '100%': { transform: 'translateX(0)', opacity: '1' }
        },
        'slide-up': {
          '0%': {
            opacity: '0',
            transform: 'translateY(10px) scale(0.95)'
          },
          '100%': {
            opacity: '1',
            transform: 'translateY(0) scale(1)'
          }
        },
        'expand-width': {
          '0%': { width: '0' },
          '100%': { width: '30%' }
        }
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out',
        'bounce-subtle': 'bounce-subtle 0.3s cubic-bezier(0.34, 1.56, 0.64, 1)',
        'command-shift': 'command-shift 0.3s cubic-bezier(0.34, 1.56, 0.64, 1)',
        'slide-up': 'slide-up 0.2s cubic-bezier(0.34, 1.56, 0.64, 1)',
        'expand-width': 'expand-width 0.3s cubic-bezier(0.34, 1.56, 0.64, 1)'
      },
      transitionProperty: {
        'width-padding': 'width, padding'
      }
    }
  },
  safelist: [
    {
      pattern:
        /^(filter-none|blur(?:-\w+)?|brightness-\d+|contrast-\d+|grayscale(?:-\d+)?|hue-rotate-\d+|-hue-rotate-\d+|invert(?:-\d+)?|saturate-\d+|sepia(?:-\d+)?)$/
    },
    // Custom elements (`public/elements/*.jsx`) are compiled in the browser
    // against this stylesheet, so a utility they use exists only if the
    // application happens to use it too. The layout, spacing and type
    // utilities below are kept regardless: a host's card must not lose its
    // grid because a feature that used `grid-cols-2` was deleted here.
    {
      pattern:
        /^(grid-cols-(?:[1-9]|1[0-2])|col-span-(?:[1-9]|1[0-2])|gap(?:-[xy])?-(?:0|0\.5|1|1\.5|2|2\.5|3|3\.5|4|5|6|8)|-?[mp][trblxy]?-(?:0|0\.5|1|1\.5|2|2\.5|3|3\.5|4|5|6|8|10|12|auto)|space-[xy]-(?:0|0\.5|1|1\.5|2|3|4)|[hw]-(?:0|0\.5|1|1\.5|2|3|4|5|6|8|10|12|16|20|24|32|40|48|64|full|auto|fit|screen)|(?:min|max)-w-(?:0|full|xs|sm|md|lg|xl|2xl|none|fit)|tracking-(?:tighter|tight|normal|wide|wider|widest)|leading-(?:none|tight|snug|normal|relaxed|loose)|font-(?:thin|light|normal|medium|semibold|bold)|text-(?:xs|sm|base|lg|xl|2xl|left|center|right)|rounded(?:-[tblr]|-[tb][lr])?(?:-(?:none|sm|md|lg|xl|2xl|full))?|(?:items|self)-(?:start|end|center|stretch|baseline)|justify-(?:start|end|center|between|around|evenly)|flex-(?:row|col|wrap|nowrap|1|auto|none|shrink-0|grow)|line-clamp-[1-6]|truncate|whitespace-(?:nowrap|pre-wrap|normal)|overflow-(?:hidden|auto|x-auto|y-auto))$/
    },
    {
      pattern:
        /^(opacity-(?:0|10|20|30|40|50|60|70|80|90|100)|(?:border|text|bg)-(?:primary|muted|accent|destructive)(?:-foreground)?(?:\/(?:10|20|30|40|50|60|70|80|90))?)$/,
      variants: ['hover', 'disabled']
    }
  ],
  plugins: [animate]
};
