/* Polyfill browser DOMParser for @ifc-lite/cli IDS validation in Node (no edits to upstream). */
const { JSDOM } = require("jsdom");
const { DOMParser } = new JSDOM().window;
global.DOMParser = DOMParser;
