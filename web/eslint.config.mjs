import next from "eslint-config-next";

const config = [{ ignores: [".next/**", "out/**", "node_modules/**"] }, ...next];

export default config;
