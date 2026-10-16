// Which of $keys are Module nodes; the decisions job drops about_paths that map to none.
MATCH (m:Module) WHERE m.key IN $keys
RETURN m.key AS key
