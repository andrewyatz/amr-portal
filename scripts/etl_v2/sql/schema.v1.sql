CREATE TABLE dataset(
    dataset_id BIGINT NOT NULL,
    "name" VARCHAR NOT NULL,
    PRIMARY KEY(dataset_id)
);

CREATE TABLE column_definition(
    column_id BIGINT NOT NULL,
    fullname VARCHAR NOT NULL,
    "name" VARCHAR NOT NULL,
    "label" VARCHAR NOT NULL,
    "type" VARCHAR NOT NULL,
    sortable BOOLEAN NOT NULL,
    url VARCHAR,
    "delimiter" VARCHAR,
    PRIMARY KEY(column_id)
);

CREATE TABLE dataset_column(
    dataset_id BIGINT NOT NULL, 
    column_id BIGINT NOT NULL,
    FOREIGN KEY (dataset_id) REFERENCES dataset(dataset_id),
    FOREIGN KEY (column_id) REFERENCES column_definition(column_id)
);

CREATE TABLE IF NOT EXISTS "view"(
    view_id INTEGER PRIMARY KEY,
    url_name VARCHAR NOT NULL,
    "name" VARCHAR NOT NULL
);

CREATE TABLE view_column(
    view_id INTEGER NOT NULL,
    column_id BIGINT NOT NULL,
    rank INTEGER,
    enable_by_default BOOLEAN NOT NULL,
    FOREIGN KEY (view_id) REFERENCES view(view_id),
    FOREIGN KEY (column_id) REFERENCES column_definition(column_id)
);

CREATE TABLE category(
    category_id BIGINT NOT NULL,
    dataset_id BIGINT NOT NULL,
    column_id BIGINT NOT NULL,
    title VARCHAR NOT NULL,
    "name" VARCHAR NOT NULL,
    PRIMARY KEY(category_id),
    FOREIGN KEY (dataset_id) REFERENCES dataset(dataset_id),
    FOREIGN KEY (column_id) REFERENCES column_definition(column_id)
);

CREATE TABLE category_group(
    category_group_id INTEGER PRIMARY KEY,
    "name" VARCHAR NOT NULL,
    is_primary BOOLEAN NOT NULL,
    view_id INTEGER NOT NULL,
    FOREIGN KEY (view_id) REFERENCES view(view_id)
);

CREATE TABLE category_group_category(
    category_group_id INTEGER NOT NULL,
    category_id BIGINT NOT NULL,
    FOREIGN KEY (category_group_id) REFERENCES category_group(category_group_id),
    FOREIGN KEY (category_id) REFERENCES category(category_id)
);

CREATE TABLE IF NOT EXISTS "filter"(
    column_id BIGINT NOT NULL,
    "value" VARCHAR NOT NULL,
    "label" VARCHAR NOT NULL,
    FOREIGN KEY (column_id) REFERENCES column_definition(column_id)
);

CREATE TABLE IF NOT EXISTS "release"(release_label VARCHAR NOT NULL);

CREATE VIEW view_categories AS
SELECT v.view_id,
    v.url_name AS view_url_name,
    v."name" AS view_name,
    cg.category_group_id,
    cg."name" AS category_group_name,
    cg.is_primary AS category_group_is_primary,
    c.title AS category_name,
    c.column_id,
    cd."name" AS column_name,
    cd.fullname AS column_fullname,
    d."name" AS dataset_name
FROM category_group AS cg
    INNER JOIN category_group_category AS cgc ON ((cgc.category_group_id = cg.category_group_id))
    INNER JOIN category AS c ON ((cgc.category_id = c.category_id))
    INNER JOIN view_column AS vc ON (
        (
            (cg.view_id = vc.view_id)
            AND (c.column_id = vc.column_id)
        )
    )
    INNER JOIN "view" AS v ON ((vc.view_id = v.view_id))
    INNER JOIN column_definition AS cd ON ((c.column_id = cd.column_id))
    INNER JOIN dataset AS d ON ((d.dataset_id = c.dataset_id))
ORDER BY v.view_id,
    cg.category_group_id;

-- Never used
-- CREATE VIEW view_categories_json AS
-- SELECT CAST(
--         main.struct_pack(
--             view_id := view_id,
--             view_url_name := view_url_name,
--             category_group_id := category_group_id,
--             view_name := view_name,
--             category_group_name := category_group_name,
--             category_group_is_primary := category_group_is_primary,
--             category_name := category_name,
--             column_id := column_id,
--             column_name := column_name,
--             column_fullname := column_fullname,
--             dataset_name := dataset_name
--         ) AS "JSON"
--     ) AS "json"
-- FROM view_categories;
