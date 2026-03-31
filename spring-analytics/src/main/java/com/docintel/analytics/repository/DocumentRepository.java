package com.docintel.analytics.repository;
import com.docintel.analytics.model.ProcessedDocument;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;
@Repository
public interface DocumentRepository extends JpaRepository<ProcessedDocument, Long> {}
