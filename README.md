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

##### `/filters-config`
```
curl -X 'GET' \
  'http://localhost:8000/filters-config' \
  -H 'accept: application/json'
```

##### `/amr-records`
###### Fetching _Phenotype_ data
```
curl -X 'POST' \
  'http://localhost:8000/amr-records' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "selected_filters": [
    { "category": "phenotype-genus", "value": "Streptococcus" },
    { "category": "phenotype-Antibiotic_abbreviation", "value": "OXA" },
    { "category": "phenotype-Antibiotic_abbreviation", "value": "AMK" }
  ],
  "view_id": 1,
  "order_by": {
    "category": "phenotype-collection_date",
    "order": "DESC"
  }
}'
```

###### Fetching _Genotype_ data
```
curl -X 'POST' \
  'http://localhost:8000/amr-records' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "selected_filters": [
    { "category": "genotype-Contig_id", "value": "CQKJ01000001.1" },
    { "category": "genotype-Contig_id", "value": "DAFBZU010000245.1" }
  ],
  "view_id": 2,
  "order_by": {
    "category": "genotype-Contig_id",
    "order": "DESC"
  }
}'
```

##### `/download`
###### Download data in the current page in `CSV` format
```
curl -X 'POST' \
  'http://localhost:8000/amr-records/download' \
  -H 'Content-Type: application/json' \
  -d '{
  "selected_filters": [
    { "category": "phenotype-genus", "value": "Streptococcus" },
    { "category": "phenotype-Antibiotic_abbreviation", "value": "OXA" },
    { "category": "phenotype-Antibiotic_abbreviation", "value": "AMK" }
  ],
  "view_id": 1,
  "order_by": {
    "category": "phenotype-collection_date",
    "order": "DESC"
  }
}'
```

###### Download all matches in `JSON` format
```
curl -X 'POST' \
  'http://localhost:8000/amr-records/download?scope=all&file_format=json' \
  -H 'Content-Type: application/json' \
  -d '{
  "selected_filters": [
    { "category": "phenotype-genus", "value": "Streptococcus" }
  ],
  "view_id": 1,
  "order_by": {
    "category": "phenotype-collection_date",
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

## Data connect

This API can support the GA4GH Data Connect product. See [DATACONNECT.md](backend/DATACONNECT.md) for details. To enable Data Connect add the environment variable.

```bash
export ENABLE_DATACONNECT=1
uvicorn backend.main:app --reload
```

Data Connect will be available under the `/dataconnect` URL prefix.
