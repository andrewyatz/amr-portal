# amr-portal
A portal for viewing antimicrobial resistance (AMR) data

## Local setup

```
git clone https://github.com/Ensembl/amr-portal.git
cd amr-portal
```

### Backend

1. Install requirements
```
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

2. Create a .env File
```
# .env
DUCKDB_PATH=path/to/duckdb/file.duckdb
```

Or

```shell
export DUCKDB_PATH=path/to/duckdb/file.duckdb
```

```shell
# make sure you're in the *root directory*
cd ..
uvicorn backend.main:app --reload
```

Swagger UI: http://localhost:8000/docs

For production use:
```shell
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 4
```

For testing:

Create a `.env.test` file in the project root with the following content:
```
DUCKDB_PATH=path/to/test_data.duckdb
```

Then run:
```shell
TESTING=true pytest backend/
```

#### API Calls Examples

##### `/api/filters-config`
```
curl -X 'GET' \
  'http://localhost:8000/api/filters-config' \
  -H 'accept: application/json'
```

##### `/api/amr-records`
###### Fetching _Phenotype_ data
```
curl -X 'POST' \
  'http://localhost:8000/api/amr-records' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "selected_filters": [
    { "category": "phenotype-genus", "value": "Streptococcus" },
    { "category": "phenotype-antibiotic_name", "value": "oxacillin" },
    { "category": "phenotype-antibiotic_name", "value": "amikacin" }
  ],
  "view_id": 1,
  "order_by": {
    "category": "phenotype-collection_year",
    "order": "DESC"
  }
}'
```

###### Fetching _Genotype_ data
```
curl -X 'POST' \
  'http://localhost:8000/api/amr-records' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "selected_filters": [
    { "category": "genotype-genus", "value": "Staphylococcus" },
    { "category": "genotype-species", "value": "aureus" }
  ],
  "view_id": 2,
  "order_by": {
    "category": "genotype-genus",
    "order": "DESC"
  }
}'
```

##### `/api/amr-records/download`
###### Download data in the current page in `CSV` format
```
curl -X 'POST' \
  'http://localhost:8000/api/amr-records/download' \
  -H 'Content-Type: application/json' \
  -d '{
  "selected_filters": [
    { "category": "phenotype-genus", "value": "Streptococcus" },
    { "category": "phenotype-antibiotic_name", "value": "oxacillin" },
    { "category": "phenotype-antibiotic_name", "value": "amikacin" }
  ],
  "view_id": 1,
  "order_by": {
    "category": "phenotype-collection_year",
    "order": "DESC"
  }
}'
```

###### Download all matches in `JSON` format
```
curl -X 'POST' \
  'http://localhost:8000/api/amr-records/download?scope=all&file_format=json' \
  -H 'Content-Type: application/json' \
  -d '{
  "selected_filters": [
    { "category": "phenotype-genus", "value": "Streptococcus" }
  ],
  "view_id": 1,
  "order_by": {
    "category": "phenotype-collection_year",
    "order": "DESC"
  }
}'
```

### Frontend

Prerequisite: need to have Node installed. As a rule of thumb, always use the latest LTS version of Node.

```
cd frontend
npm install
npm run dev
```

## Production build

### Frontend

```
cd frontend
npm install
npm run build
```

This will create a `dist` directory containing a static html file and all the assets that it loads.

## Further details

The frontend combines a static site built with [Eleventy](https://www.11ty.dev/) with islands of interactivity built with web components and Lit. For more details about the build setup, see `frontend/eleventy.config.js`, and the documentation in the [`docs/decisions`](/docs//decisions/) directory.
