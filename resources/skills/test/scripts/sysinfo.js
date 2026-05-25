const os = require('os');

console.log(`Node: ${process.version}`);
console.log(`OS: ${os.type()} ${os.release()}`);
console.log(`Args: ${process.argv.slice(2).join(' ')}`);
