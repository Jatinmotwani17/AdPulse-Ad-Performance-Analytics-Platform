/*
Query 1: Top 10 keywords by traffic-to-cost efficiency.
Business question:
Which keywords deliver the most traffic per unit of spend so we can prioritize efficient terms?
*/
SELECT
    w.keyword,
    SUM(w.Traffic) AS total_traffic,
    SUM(w.Traffic_Cost) AS total_cost,
    ROUND(SUM(w.Traffic) / SUM(w.Traffic_Cost), 4) AS traffic_to_cost_ratio
FROM website_traffic_data AS w
WHERE w.Traffic_Cost > 0
GROUP BY w.keyword
ORDER BY traffic_to_cost_ratio DESC
LIMIT 10;


/*
Query 2: Keywords where position improved vs previous position.
Business question:
Which keywords are improving in ad rank, and by how much, so we can identify positive momentum?
*/
SELECT
    w.keyword,
    w.positions,
    w.previous_positions,
    (w.previous_positions - w.positions) AS improvement_magnitude,
    w.last_seen
FROM website_traffic_data AS w
WHERE w.positions < w.previous_positions
ORDER BY improvement_magnitude DESC, w.last_seen DESC;


/*
Query 3: CPC tier segmentation with count and average traffic.
Business question:
How do keyword volumes and traffic outcomes differ across low, medium, and high CPC bands?
*/
SELECT
    CASE
        WHEN w.CPC < 1 THEN 'Low'
        WHEN w.CPC BETWEEN 1 AND 3 THEN 'Medium'
        ELSE 'High'
    END AS cpc_tier,
    COUNT(*) AS keyword_count,
    ROUND(AVG(w.Traffic), 2) AS avg_traffic
FROM website_traffic_data AS w
GROUP BY cpc_tier
ORDER BY
    CASE cpc_tier
        WHEN 'Low' THEN 1
        WHEN 'Medium' THEN 2
        ELSE 3
    END;


-- ============================================================
-- 4. MONTHLY TRAFFIC TREND
-- ============================================================
-- Business Question:
-- How does traffic and advertising cost change month by month?
--
-- This helps identify changes in traffic, advertising spend,
-- and CPC over time.
-- ============================================================

SELECT
    DATE_FORMAT(last_seen, '%Y-%m') AS month,
    SUM(Traffic) AS total_traffic,
    SUM(Traffic_Cost) AS total_cost,
    ROUND(AVG(CPC), 2) AS avg_cpc
FROM website_traffic_data
GROUP BY DATE_FORMAT(last_seen, '%Y-%m')
ORDER BY DATE_FORMAT(last_seen, '%Y-%m');

/*
Query 5: High-volume but low-traffic keywords (missed opportunities).
Business question:
Which keywords have above-average search demand but below-average traffic, indicating optimization opportunities?
*/
SELECT
    k.keyword_ID,
    k.keyword,
    sv.search_volume,
    perf.total_traffic
FROM keyword AS k
JOIN search_volume AS sv
    ON sv.keyword_ID = k.keyword_ID
JOIN (
    SELECT
        keyword_ID,
        SUM(Traffic) AS total_traffic
    FROM website_traffic_data
    GROUP BY keyword_ID
) AS perf
    ON perf.keyword_ID = k.keyword_ID
WHERE sv.search_volume > (
    SELECT AVG(search_volume) FROM search_volume
)
AND perf.total_traffic < (
    SELECT AVG(keyword_traffic.total_traffic)
    FROM (
        SELECT keyword_ID, SUM(Traffic) AS total_traffic
        FROM website_traffic_data
        GROUP BY keyword_ID
    ) AS keyword_traffic
)
ORDER BY sv.search_volume DESC, perf.total_traffic ASC;


/*
Query 6: Keyword difficulty vs traffic performance.
Business question:
How does performance vary by difficulty level, and where should we focus effort by keyword class?
*/
SELECT
    kd.difficulty_level,
    COUNT(*) AS record_count,
    ROUND(AVG(w.Traffic), 2) AS avg_traffic,
    ROUND(AVG(w.CPC), 2) AS avg_cpc
FROM website_traffic_data AS w
JOIN keyword_difficulty AS kd
    ON kd.keyword_ID = w.keyword_ID
GROUP BY kd.difficulty_level
ORDER BY avg_traffic DESC;


/*
Query 7: Top 5 most expensive keywords with difficulty label and current position.
Business question:
Which keywords consume the most budget, and what is their latest rank and difficulty profile?
*/
SELECT
    cost.keyword_ID,
    k.keyword,
    cost.total_traffic_cost,
    kd.difficulty_level,
    latest.positions AS current_position
FROM (
    SELECT
        keyword_ID,
        SUM(Traffic_Cost) AS total_traffic_cost
    FROM website_traffic_data
    GROUP BY keyword_ID
    ORDER BY total_traffic_cost DESC
    LIMIT 5
) AS cost
JOIN keyword AS k
    ON k.keyword_ID = cost.keyword_ID
JOIN keyword_difficulty AS kd
    ON kd.keyword_ID = cost.keyword_ID
JOIN website_traffic_data AS latest
    ON latest.keyword_ID = cost.keyword_ID
   AND latest.last_seen = (
       SELECT MAX(w2.last_seen)
       FROM website_traffic_data AS w2
       WHERE w2.keyword_ID = cost.keyword_ID
   )
ORDER BY cost.total_traffic_cost DESC;


/*
Query 8: Competition score bucketing with avg CPC and total traffic.
Business question:
How do performance and costs differ by low/medium/high competition bands?
*/
SELECT
    CASE
        WHEN w.Competition < 0.33 THEN 'Low'
        WHEN w.Competition <= 0.66 THEN 'Medium'
        ELSE 'High'
    END AS competition_bucket,
    ROUND(AVG(w.CPC), 2) AS avg_cpc,
    SUM(w.Traffic) AS total_traffic
FROM website_traffic_data AS w
GROUP BY competition_bucket
ORDER BY
    CASE competition_bucket
        WHEN 'Low' THEN 1
        WHEN 'Medium' THEN 2
        ELSE 3
    END;
