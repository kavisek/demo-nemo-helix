CREATE TABLE alembic_version (
	version_num VARCHAR(32) NOT NULL, 
	CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);
CREATE TABLE workspaces (
	id VARCHAR(255) NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	description TEXT, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	created_by VARCHAR(255), 
	updated_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	updated_by VARCHAR(255), deletion_stage VARCHAR(100), 
	PRIMARY KEY (id), 
	UNIQUE (name)
);
CREATE INDEX idx_workspaces_created ON workspaces (created_at);
CREATE TABLE entities (
	id VARCHAR(255) NOT NULL, 
	workspace VARCHAR(255) NOT NULL, 
	entity_type VARCHAR(255) NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	parent VARCHAR(255), 
	project VARCHAR(255), 
	data JSON NOT NULL, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	created_by VARCHAR(255), 
	updated_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	updated_by VARCHAR(255), 
	db_version INTEGER DEFAULT '1' NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(parent) REFERENCES entities (id) ON DELETE CASCADE, 
	FOREIGN KEY(workspace) REFERENCES workspaces (name) ON DELETE CASCADE
);
CREATE INDEX idx_entities_created ON entities (created_at);
CREATE INDEX idx_entities_lookup ON entities (workspace, entity_type, name);
CREATE INDEX idx_entities_parent ON entities (parent);
CREATE INDEX idx_entities_project ON entities (project);
CREATE INDEX idx_entities_type ON entities (workspace, entity_type);
CREATE INDEX idx_entities_updated ON entities (updated_at);
CREATE INDEX idx_entities_workspace ON entities (workspace);
CREATE UNIQUE INDEX uq_entities_child ON entities (workspace, entity_type, parent, name) WHERE parent IS NOT NULL;
CREATE UNIQUE INDEX uq_entities_root ON entities (workspace, entity_type, name) WHERE parent IS NULL;
CREATE TABLE accounts (
	id VARCHAR(255) NOT NULL, 
	type VARCHAR(32) NOT NULL, 
	link_key VARCHAR(512), 
	display_name VARCHAR(512), 
	primary_email VARCHAR(320), 
	status VARCHAR(32) DEFAULT 'active' NOT NULL, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	updated_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX uq_accounts_active_link_key ON accounts (link_key) WHERE link_key IS NOT NULL AND status = 'active';
CREATE INDEX idx_accounts_status ON accounts (status);
CREATE INDEX idx_accounts_type ON accounts (type);
CREATE TABLE account_identities (
	id VARCHAR(255) NOT NULL, 
	account_id VARCHAR(255) NOT NULL, 
	issuer VARCHAR(512) NOT NULL, 
	subject VARCHAR(512) NOT NULL, 
	subject_claim VARCHAR(128) NOT NULL, 
	status VARCHAR(32) DEFAULT 'active' NOT NULL, 
	linked_via VARCHAR(64) NOT NULL, 
	linked_by VARCHAR(255), 
	linked_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
	last_seen_at DATETIME, 
	link_key_claim_at_link_time VARCHAR(128), 
	link_key_value_at_link_time VARCHAR(512), 
	claims_snapshot JSON NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(account_id) REFERENCES accounts (id) ON DELETE CASCADE, 
	CONSTRAINT uq_account_identities_issuer_subject UNIQUE (issuer, subject)
);
CREATE INDEX idx_account_identities_account_id ON account_identities (account_id);
CREATE INDEX idx_account_identities_status ON account_identities (status);
