import { HtmlBasePlugin } from '@11ty/eleventy';

import { buildAssets } from './utils/build.js';
import { getAssetOutputPath } from './utils/eleventy-filters.js';
import { documentationPageTransform } from './utils/documentation-page-transform.js';

const pathPrefix = '/amr/';
// const pathPrefix = undefined;
const outputRoot = 'dist';

export default async function(eleventyConfig) {
  // Hook into Eleventy's data pipeline at an early stage of a build,
  // and build the static assets. Add the assets manifest to global data,
  // such that it can be used in templates to retrieve paths to built files.
  eleventyConfig.addGlobalData('assetsManifest', async () => {
    const assetsManifest = await buildAssets({
      pathPrefix,
      outputRoot
    });
    console.log(`[build:assets]: added assets manifest to global data`, assetsManifest);
    return assetsManifest;
  });

  // Trigger rebuild if script or style files have been updated
  eleventyConfig.addWatchTarget('src/assets/scripts');
  eleventyConfig.addWatchTarget('src/assets/css');

  eleventyConfig.addPassthroughCopy({
    'src/assets/images': 'assets/images',
    'src/assets/docs': 'assets/docs'
  });
  // Copy fonts distributed via npm
  eleventyConfig.addPassthroughCopy({
    'node_modules/@ensembl/ensembl-elements-common/fonts': 'assets/fonts',
  });

  eleventyConfig.addFilter('getAssetOutputPath', getAssetOutputPath);

  eleventyConfig.addFilter('sortByTitle', values => {
    return values.toSorted((a, b) => a.data.title.localeCompare(b.data.title));
  });

  eleventyConfig.addPlugin(HtmlBasePlugin);

  eleventyConfig.addTransform('documentation-page-transform', documentationPageTransform);
};


export const config = {
  dir: {
    input: 'src/content',
    // REMEMBER: includes, layouts, and data directories are registered relative to the input directory
    includes: '../_includes',
    output: outputRoot
  },
  templateFormats: ['html', 'njk', 'md', '11ty.js'],
  markdownTemplateEngine: 'njk',
	htmlTemplateEngine: 'njk',
  pathPrefix
};
