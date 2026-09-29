# Schema Audit — 2026-09-29T19:52:12.109313+00:00

**Status: MATCH** · target `postgresql://postgres@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres` · role `postgres`

## errors (0)
_none_

## warnings (12)
- type drift: publications.open_access live=text spec=varchar
- type drift: publications.publisher live=text spec=varchar
- type drift: publications.source live=varchar spec=text
- type drift: authors.author_name live=text spec=varchar
- type drift: institutions.city live=text spec=varchar
- type drift: institutions.country live=text spec=varchar
- type drift: keywords.keyword_id live=varchar spec=bigint
- type drift: keywords.keyword live=text spec=varchar
- type drift: funding.funding_id live=varchar spec=bigint
- type drift: funding.grant_number live=text spec=varchar
- type drift: chunks.chunk_id live=varchar spec=bigint
- type drift: chunks.section live=text spec=varchar

## notes (10)
- extra column (prototype-tolerated): publications.source_title
- extra column (prototype-tolerated): publications.link
- extra column (prototype-tolerated): publications.issn
- extra column (prototype-tolerated): publications.search_text
- extra column (prototype-tolerated): funding.source_text
- extra column (prototype-tolerated): chunks.source_type
- extra column (prototype-tolerated): chunks.embedding
- extra column (prototype-tolerated): chunks.embedding_model
- extra column (prototype-tolerated): chunks.embedding_version
- extra column (prototype-tolerated): chunks.embedding_dimension

