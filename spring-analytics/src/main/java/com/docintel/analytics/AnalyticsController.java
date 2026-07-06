package com.docintel.analytics;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.math.BigDecimal;
import java.sql.Array;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.*;


@RestController
@RequestMapping("/api/v1")
public class AnalyticsController {

    private static final String DOC_COLS =
        "task_id, filename, status, vendor_name, total_amount, doc_date, " +
        "overall_confidence, review_reasons, created_at";

    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper = new ObjectMapper();

    public AnalyticsController(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    private Object parseJson(String raw) {
        if (raw == null) return null;
        try {
            return mapper.readValue(raw, Object.class);
        } catch (Exception e) {
            return raw;
        }
    }

    private List<String> toList(Array array) throws SQLException {
        if (array == null) return List.of();
        Object[] vals = (Object[]) array.getArray();
        List<String> out = new ArrayList<>(vals.length);
        for (Object v : vals) out.add(v == null ? null : v.toString());
        return out;
    }

    private Map<String, Object> docRow(ResultSet rs, int rowNum) throws SQLException {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("task_id", rs.getString("task_id"));
        m.put("filename", rs.getString("filename"));
        m.put("status", rs.getString("status"));
        m.put("vendor_name", rs.getString("vendor_name"));
        m.put("total_amount", rs.getBigDecimal("total_amount"));
        m.put("doc_date", rs.getString("doc_date"));
        m.put("overall_confidence", rs.getBigDecimal("overall_confidence"));
        m.put("review_reasons", toList(rs.getArray("review_reasons")));
        m.put("created_at", rs.getString("created_at"));
        return m;
    }

    @GetMapping("/documents")
    public List<Map<String, Object>> documents(
            @RequestParam(required = false) String status,
            @RequestParam(defaultValue = "100") int limit) {
        if (status != null && !status.isBlank()) {
            return jdbc.query(
                "SELECT " + DOC_COLS + " FROM documents WHERE status = ?::doc_status " +
                "ORDER BY created_at DESC LIMIT ?",
                this::docRow, status, limit);
        }
        return jdbc.query(
            "SELECT " + DOC_COLS + " FROM documents ORDER BY created_at DESC LIMIT ?",
            this::docRow, limit);
    }

    @GetMapping("/documents/{id}")
    public Map<String, Object> document(@PathVariable String id) {
        List<Map<String, Object>> rows = jdbc.query(
            "SELECT " + DOC_COLS + ", confidence, structured_data " +
            "FROM documents WHERE task_id = ?::uuid",
            (rs, n) -> {
                Map<String, Object> m = docRow(rs, n);
                m.put("confidence", parseJson(rs.getString("confidence")));
                m.put("structured_data", parseJson(rs.getString("structured_data")));
                return m;
            }, id);

        if (rows.isEmpty()) return Map.of("error", "not found");

        Map<String, Object> doc = rows.get(0);
        doc.put("line_items", jdbc.query(
            "SELECT description, amount FROM line_items WHERE task_id = ?::uuid",
            (rs, n) -> {
                Map<String, Object> li = new LinkedHashMap<>();
                li.put("description", rs.getString("description"));
                li.put("amount", rs.getBigDecimal("amount"));
                return li;
            }, id));
        return doc;
    }

    @GetMapping("/review")
    public List<Map<String, Object>> review() {
        return jdbc.query(
            "SELECT " + DOC_COLS + " FROM documents WHERE status = 'NEEDS_REVIEW' " +
            "ORDER BY overall_confidence ASC NULLS FIRST",
            this::docRow);
    }

    @GetMapping("/analytics/summary")
    public Map<String, Object> summary() {
        Map<String, Object> counts = new LinkedHashMap<>();
        for (Map<String, Object> r : jdbc.queryForList(
                "SELECT status, count(*) AS c FROM documents GROUP BY status")) {
            counts.put((String) r.get("status"), r.get("c"));
        }

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("status_counts", counts);
        out.put("documents_total",
            jdbc.queryForObject("SELECT count(*) FROM documents", Long.class));
        out.put("total_spend", jdbc.queryForObject(
            "SELECT coalesce(sum(total_amount), 0) FROM documents WHERE status = 'COMPLETED'",
            BigDecimal.class));
        out.put("avg_confidence", jdbc.queryForObject(
            "SELECT coalesce(round(avg(overall_confidence), 3), 0) FROM documents " +
            "WHERE overall_confidence IS NOT NULL", BigDecimal.class));
        return out;
    }

    @GetMapping("/analytics/by-vendor")
    public List<Map<String, Object>> byVendor() {
        return jdbc.query(
            "SELECT vendor_name, count(*) AS doc_count, " +
            "coalesce(sum(total_amount), 0) AS total_spend, " +
            "round(avg(total_amount), 2) AS avg_amount " +
            "FROM documents WHERE status = 'COMPLETED' AND vendor_name IS NOT NULL " +
            "GROUP BY vendor_name ORDER BY total_spend DESC",
            (rs, n) -> {
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("vendor_name", rs.getString("vendor_name"));
                m.put("doc_count", rs.getLong("doc_count"));
                m.put("total_spend", rs.getBigDecimal("total_spend"));
                m.put("avg_amount", rs.getBigDecimal("avg_amount"));
                return m;
            });
    }

    @GetMapping("/analytics/monthly")
    public List<Map<String, Object>> monthly() {
        return jdbc.query(
            "SELECT to_char(date_trunc('month', doc_date), 'YYYY-MM') AS month, " +
            "count(*) AS doc_count, coalesce(sum(total_amount), 0) AS total_spend " +
            "FROM documents WHERE status = 'COMPLETED' AND doc_date IS NOT NULL " +
            "GROUP BY 1 ORDER BY 1",
            (rs, n) -> {
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("month", rs.getString("month"));
                m.put("doc_count", rs.getLong("doc_count"));
                m.put("total_spend", rs.getBigDecimal("total_spend"));
                return m;
            });
    }

    @GetMapping("/analytics/anomalies")
    public List<Map<String, Object>> anomalies(
            @RequestParam(defaultValue = "3.0") double factor) {
        return jdbc.query(
            "WITH va AS (" +
            "  SELECT vendor_name, avg(total_amount) AS avg_amt, count(*) AS n " +
            "  FROM documents WHERE status = 'COMPLETED' AND vendor_name IS NOT NULL " +
            "  GROUP BY vendor_name) " +
            "SELECT d.task_id, d.vendor_name, d.total_amount, " +
            "       round(va.avg_amt, 2) AS vendor_avg " +
            "FROM documents d JOIN va ON d.vendor_name = va.vendor_name " +
            "WHERE d.status = 'COMPLETED' AND va.n >= 2 " +
            "  AND d.total_amount > va.avg_amt * ? " +
            "ORDER BY d.total_amount DESC",
            (rs, n) -> {
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("task_id", rs.getString("task_id"));
                m.put("vendor_name", rs.getString("vendor_name"));
                m.put("total_amount", rs.getBigDecimal("total_amount"));
                m.put("vendor_avg", rs.getBigDecimal("vendor_avg"));
                return m;
            }, factor);
    }

    @GetMapping("/healthz")
    public Map<String, Object> health() {
        jdbc.queryForObject("SELECT 1", Integer.class);
        return Map.of("status", "ok");
    }
}
