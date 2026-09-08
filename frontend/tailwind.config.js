/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: { ink: "#08111f", panel: "#101d2e", cyan: "#5eead4", amber: "#fbbf24" },
      boxShadow: { glow: "0 0 30px rgba(94, 234, 212, 0.12)" },
    },
  },
  plugins: [],
};
