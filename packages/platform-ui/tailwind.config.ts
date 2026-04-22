import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}'
  ],
  theme: {
    extend: {
      colors: {
        primary: '#1B4F8A',
        secondary: '#2D9CDB',
        accent: '#00B4D8',
        dark: '#1A1A2E',
        gray: '#6B7280',
        lightGray: '#E5E7EB',
        tableAlt: '#EFF6FF',
        green: '#059669',
        warning: '#D97706',
        red: '#DC2626'
      },
      fontFamily: {
        sans: ['Inter', 'sans-serif']
      }
    }
  },
  plugins: []
}

export default config
