const chokidar = require('chokidar');
const { exec } = require('child_process');

const inputFile = 'topography_colour.dxf';
const outputFile = 'static/scene.json';

console.log(`Watching for changes to ${inputFile}...`);

chokidar.watch(inputFile).on('change', (path) => {
  console.log(`File ${path} has changed. Re-running conversion...`);
   
  exec(`node convert_dxf_to_json.js ${inputFile} ${outputFile}`, (error, stdout, stderr) => {
    if (error) {
      console.error(`Conversion error: ${error.message}`);
      return;
    }
    if (stderr) {
      console.error(`Conversion stderr: ${stderr}`);
      return;
    }
    console.log(`Conversion successful: ${stdout.trim()}`);
  });
});
