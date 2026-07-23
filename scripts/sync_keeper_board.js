#!/usr/bin/env node

const fs = require('fs');
const path = require('path');

// Paths
const exportsDir = path.join(__dirname, '../data/processed/keeper_exports');
const boardFile = path.join(__dirname, '../data/processed/keeper_board.html');

// Parse filename: keepers_YYYYMMDD_METHOD.csv or keepers_YYYYMMDD_HHMM.csv
function parseFilename(filename) {
    const match = filename.match(/^keepers_(\d{8})_([a-z0-9-]+)\.csv$/i);
    if (!match) return null;

    const date = match[1];
    const methodPart = match[2];

    // Method detection: if it's all digits, it's a time (old format)
    let method = 'unknown';
    let label = filename;

    if (/^\d{4}$/.test(methodPart)) {
        // Old format: keepers_YYYYMMDD_HHMM.csv
        method = 'rank-first';
        const time = methodPart;
        label = `${date.slice(4, 6)}/${date.slice(6, 8)} - ${time.slice(0, 2)}:${time.slice(2, 4)}`;
    } else {
        // New format: keepers_YYYYMMDD_METHOD.csv
        method = methodPart;
        const year = date.slice(0, 4);
        const month = date.slice(4, 6);
        const day = date.slice(6, 8);
        label = `${month}/${day} - ${method}`;
    }

    return {
        date,
        method,
        file: filename,
        label
    };
}

// Generate versions array
function generateVersionsArray() {
    if (!fs.existsSync(exportsDir)) {
        console.error(`Error: ${exportsDir} not found`);
        process.exit(1);
    }

    const files = fs.readdirSync(exportsDir).filter(f => f.endsWith('.csv'));
    if (files.length === 0) {
        console.warn('No CSV files found in keeper_exports/');
        return [];
    }

    const versions = files
        .map(parseFilename)
        .filter(Boolean)
        .sort((a, b) => {
            // Sort by date descending (newest first)
            if (a.date !== b.date) return b.date.localeCompare(a.date);
            // Then by method
            return a.method.localeCompare(b.method);
        });

    return versions;
}

// Update HTML with new versions array
function updateHTML(versions) {
    let html = fs.readFileSync(boardFile, 'utf-8');

    // Generate JavaScript array
    const arrayStr = JSON.stringify(versions, null, 12);

    // Replace the versions array in the HTML
    const newHtml = html.replace(
        /const versions = \[[\s\S]*?\];/,
        `const versions = ${arrayStr};`
    );

    fs.writeFileSync(boardFile, newHtml, 'utf-8');
    console.log(`✓ Updated keeper_board.html with ${versions.length} version(s)`);

    // Log versions
    versions.forEach(v => {
        console.log(`  • ${v.label} (${v.file})`);
    });
}

// Main
const versions = generateVersionsArray();
if (versions.length > 0) {
    updateHTML(versions);
} else {
    console.warn('No keeper exports found to sync');
}
