package com.docintel.analytics.model;
import jakarta.persistence.*;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;
import java.time.LocalDateTime;

@Entity
@Table(name = "processed_documents")
public class ProcessedDocument {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY) private Long id;
    @Column(nullable = false) private String filename;
    @JdbcTypeCode(SqlTypes.JSON) @Column(name = "structured_data", columnDefinition = "jsonb") private String structuredData; 
    @Column(name = "processed_at", insertable = false, updatable = false) private LocalDateTime processedAt;
    public Long getId() { return id; }
    public String getFilename() { return filename; }
    public String getStructuredData() { return structuredData; }
}
