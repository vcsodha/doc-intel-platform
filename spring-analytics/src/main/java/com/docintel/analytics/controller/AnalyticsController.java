package com.docintel.analytics.controller;
import java.util.List;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.CrossOrigin;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.docintel.analytics.model.ProcessedDocument;
import com.docintel.analytics.repository.DocumentRepository;

@RestController
@RequestMapping("/api/v1/documents")
@CrossOrigin(origins = "*")
public class AnalyticsController {
    private final DocumentRepository repository;
    public AnalyticsController(DocumentRepository repository) { this.repository = repository; }
    @GetMapping
    public ResponseEntity<List<ProcessedDocument>> getAllDocuments() { return ResponseEntity.ok(repository.findAll()); }
}
