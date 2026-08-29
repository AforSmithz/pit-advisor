/** @type {import('next').NextConfig} */
const config = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: true,
  agentRules: false, // next 16 writes AGENTS.md and CLAUDE.md into web/ otherwise, and only the readme ships
};

export default config;
