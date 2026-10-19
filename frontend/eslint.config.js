import globals from "globals";
import pluginJs from "@eslint/js";
import pluginReactConfig from "eslint-plugin-react/configs/recommended.js";

export default [
  {languageOptions: { globals: {...globals.browser, ...globals.node} }},
  pluginJs.configs.recommended,
  pluginReactConfig,
  {
    settings: {
      react: {
        version: "detect"
      }
    },
    rules: {
      "react/prop-types": "off",
      "react/react-in-jsx-scope": "off",
      "no-unused-vars": "warn",
      "no-undef": "error"
    }
  }
];
