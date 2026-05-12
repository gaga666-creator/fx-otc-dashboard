export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      fontFamily: {
        mono: ["JetBrains Mono", "SFMono-Regular", "Consolas", "monospace"],
        sans: ["Inter", "Noto Sans TC", "system-ui", "sans-serif"],
      },
      colors: {
        ink: "#071014",
        panel: "rgba(11, 22, 28, 0.72)",
        cyanline: "#22d3ee",
        limeok: "#38d87b",
        rosebad: "#fb7185",
        amberwarn: "#fbbf24",
      },
    },
  },
  plugins: [],
};

