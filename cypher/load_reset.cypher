// Run by ingest/load.py just before REVIEW_REQUESTED is written (after nodes and other edges): it is current review load and comes only
// from the data, so every load rebuilds it (a reviewer removed from an open PR loses the edge).
MATCH (:PR)-[r:REVIEW_REQUESTED]->(:Person) DELETE r;
