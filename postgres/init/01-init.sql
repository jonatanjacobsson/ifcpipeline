-- Create clash results table
CREATE TABLE IF NOT EXISTS clash_results (
    id SERIAL PRIMARY KEY,
    original_clash_id INTEGER REFERENCES clash_results(id),
    clash_set_name TEXT NOT NULL,
    output_filename TEXT NOT NULL,
    clash_count INTEGER NOT NULL,
    clash_data JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Create indexes for faster searching
CREATE INDEX IF NOT EXISTS idx_clash_results_original_clash_id ON clash_results(original_clash_id);
CREATE INDEX IF NOT EXISTS idx_clash_results_clash_set_name ON clash_results(clash_set_name);
CREATE INDEX IF NOT EXISTS idx_clash_results_created_at ON clash_results(created_at);
CREATE INDEX IF NOT EXISTS idx_clash_data ON clash_results USING gin (clash_data);

-- Create conversion results table
CREATE TABLE IF NOT EXISTS conversion_results (
    id SERIAL PRIMARY KEY,
    input_filename TEXT NOT NULL,
    output_filename TEXT NOT NULL,
    conversion_options JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Create tester results table
CREATE TABLE IF NOT EXISTS tester_results (
    id SERIAL PRIMARY KEY,
    ifc_filename TEXT NOT NULL,
    ids_filename TEXT NOT NULL,
    test_results JSONB NOT NULL,
    pass_count INTEGER NOT NULL,
    fail_count INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Create diff results table
CREATE TABLE IF NOT EXISTS diff_results (
    id SERIAL PRIMARY KEY,
    old_file TEXT NOT NULL,
    new_file TEXT NOT NULL,
    diff_count INTEGER NOT NULL,
    diff_data JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
); 