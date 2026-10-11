// Configuration for `npx web-ext ...` run from the repository root.
// Docs: https://extensionworkshop.com/documentation/develop/web-ext-command-reference/
export default {
  sourceDir: "extension",
  artifactsDir: "web-ext-artifacts",
  build: {
    overwriteDest: true,
  },
  sign: {
    // "unlisted" = self-distribution: Mozilla signs the .xpi but does not list
    // it publicly. That is all a permanent install in release Firefox needs.
    // Use "listed" to publish on addons.mozilla.org instead.
    channel: "unlisted",
  },
};
