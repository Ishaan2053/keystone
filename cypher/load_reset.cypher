// Run by ingest/load.py before the writes: REVIEW_REQUESTED is current review load and comes only
// from the data, so every load rebuilds it (a reviewer removed from an open PR loses the edge).
MATCH (:PR)-[r:REVIEW_REQUESTED]->(:Person) DELETE r;
