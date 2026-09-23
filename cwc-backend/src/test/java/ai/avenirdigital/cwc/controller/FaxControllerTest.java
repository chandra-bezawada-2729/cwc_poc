package ai.avenirdigital.cwc.controller;

import ai.avenirdigital.cwc.model.ClassificationResultEntity;
import ai.avenirdigital.cwc.model.FaxDocumentEntity;
import ai.avenirdigital.cwc.model.RoutingDecisionEntity;
import ai.avenirdigital.cwc.repository.ClassificationResultRepository;
import ai.avenirdigital.cwc.repository.FaxDocumentRepository;
import ai.avenirdigital.cwc.repository.RoutingDecisionRepository;
import ai.avenirdigital.cwc.service.AsyncProcessingService;
import ai.avenirdigital.cwc.service.FaxIngestionService;
import ai.avenirdigital.cwc.service.FileStorageService;
import ai.avenirdigital.cwc.service.RoutingEngineService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.data.domain.PageImpl;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.web.servlet.MockMvc;

import java.io.IOException;
import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import static org.hamcrest.Matchers.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

/**
 * @WebMvcTest for FaxController — tests the HTTP layer only.
 * All service and repository dependencies are mocked (spec §13).
 */
@WebMvcTest(FaxController.class)
class FaxControllerTest {

    @Autowired MockMvc mvc;

    @MockBean FaxIngestionService            ingestionService;
    @MockBean AsyncProcessingService         asyncProcessingService;
    @MockBean FaxDocumentRepository          faxRepo;
    @MockBean ClassificationResultRepository classificationRepo;
    @MockBean RoutingDecisionRepository      routingDecisionRepo;
    @MockBean FileStorageService             fileStorageService;
    @MockBean RoutingEngineService           routingEngine;

    // ── POST /api/faxes/upload ────────────────────────────────────────────────

    @Test
    void upload_acceptsPdf_returns201() throws Exception {
        UUID tid = UUID.randomUUID();
        given(ingestionService.ingest(any(), any()))
                .willReturn(new FaxIngestionService.IngestResult(tid, "test.pdf", "RECEIVED"));

        mvc.perform(multipart("/api/faxes/upload")
                        .file(new MockMultipartFile("files", "test.pdf",
                                "application/pdf", "PDF".getBytes())))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.uploaded[0].trackingId").value(tid.toString()))
                .andExpect(jsonPath("$.uploaded[0].originalFileName").value("test.pdf"));
    }

    @Test
    void upload_ioError_returns207WithRejected() throws Exception {
        given(ingestionService.ingest(any(), any())).willThrow(new IOException("disk full"));

        mvc.perform(multipart("/api/faxes/upload")
                        .file(new MockMultipartFile("files", "bad.pdf",
                                "application/pdf", "PDF".getBytes())))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.failed[0].originalFileName").value("bad.pdf"))
                .andExpect(jsonPath("$.failed[0].reason", containsString("IO_ERROR")));
    }

    // ── GET /api/faxes ────────────────────────────────────────────────────────

    @Test
    @SuppressWarnings("unchecked")
    void list_returnsPagedResults() throws Exception {
        FaxDocumentEntity doc = buildDoc(UUID.randomUUID());

        given(faxRepo.findAll(any(Specification.class), any(Pageable.class)))
                .willReturn(new PageImpl<>(List.of(doc), PageRequest.of(0, 20), 1));
        given(classificationRepo.findAllByFaxDocumentIdIn(any()))
                .willReturn(List.of());
        given(routingDecisionRepo.findAllByFaxDocumentIdIn(any()))
                .willReturn(List.of());

        mvc.perform(get("/api/faxes"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.totalElements").value(1))
                .andExpect(jsonPath("$.content[0].processingStatus").value("RECEIVED"));
    }

    // ── GET /api/faxes/{trackingId} ───────────────────────────────────────────

    @Test
    void detail_existingDoc_returns200() throws Exception {
        UUID tid = UUID.randomUUID();
        FaxDocumentEntity doc = buildDoc(tid);
        given(faxRepo.findByTrackingId(tid)).willReturn(Optional.of(doc));
        given(classificationRepo.findByFaxDocumentId(doc.getId())).willReturn(Optional.empty());
        given(routingDecisionRepo.findTopByFaxDocumentIdOrderByDecidedAtDesc(doc.getId()))
                .willReturn(Optional.empty());

        mvc.perform(get("/api/faxes/" + tid))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.trackingId").value(tid.toString()))
                .andExpect(jsonPath("$.processingStatus").value("RECEIVED"));
    }

    @Test
    void detail_unknownTrackingId_returns404() throws Exception {
        UUID tid = UUID.randomUUID();
        given(faxRepo.findByTrackingId(tid)).willReturn(Optional.empty());

        mvc.perform(get("/api/faxes/" + tid))
                .andExpect(status().isNotFound());
    }

    @Test
    void detail_malformedTrackingId_returns400() throws Exception {
        mvc.perform(get("/api/faxes/not-a-uuid"))
                .andExpect(status().isBadRequest());
    }

    // ── POST /api/faxes/{trackingId}/confirm ─────────────────────────────────

    @Test
    void confirm_suggestedFolderMatches_returns200() throws Exception {
        UUID tid = UUID.randomUUID();
        FaxDocumentEntity doc = buildDoc(tid);
        RoutingDecisionEntity rd = buildRd(doc.getId(), "Care-Gaps", "SUGGESTED");

        given(faxRepo.findByTrackingId(tid)).willReturn(Optional.of(doc));
        given(routingDecisionRepo.findTopByFaxDocumentIdOrderByDecidedAtDesc(doc.getId()))
                .willReturn(Optional.of(rd));
        given(fileStorageService.resolve(any()))
                .willReturn(java.nio.file.Path.of("/tmp/test.pdf"));
        given(routingEngine.fileDocument(any(), any(), any(), any()))
                .willReturn(java.nio.file.Path.of("/tmp/routed/Care-Gaps/test.pdf"));

        mvc.perform(post("/api/faxes/" + tid + "/confirm")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"folder":"Care-Gaps"}
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("CONFIRMED"))
                .andExpect(jsonPath("$.folder").value("Care-Gaps"));
    }

    @Test
    void confirm_overrideWithoutReason_returns400() throws Exception {
        UUID tid = UUID.randomUUID();
        FaxDocumentEntity doc = buildDoc(tid);
        RoutingDecisionEntity rd = buildRd(doc.getId(), "Care-Gaps", "SUGGESTED");

        given(faxRepo.findByTrackingId(tid)).willReturn(Optional.of(doc));
        given(routingDecisionRepo.findTopByFaxDocumentIdOrderByDecidedAtDesc(doc.getId()))
                .willReturn(Optional.of(rd));

        mvc.perform(post("/api/faxes/" + tid + "/confirm")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"folder":"Manual-Review"}
                                """))
                .andExpect(status().isBadRequest());
    }

    @Test
    void confirm_alreadyRouted_returns409() throws Exception {
        UUID tid = UUID.randomUUID();
        FaxDocumentEntity doc = buildDoc(tid);
        RoutingDecisionEntity rd = buildRd(doc.getId(), "Care-Gaps", "CONFIRMED");
        rd.setFinalFolder("Care-Gaps");

        given(faxRepo.findByTrackingId(tid)).willReturn(Optional.of(doc));
        given(routingDecisionRepo.findTopByFaxDocumentIdOrderByDecidedAtDesc(doc.getId()))
                .willReturn(Optional.of(rd));

        mvc.perform(post("/api/faxes/" + tid + "/confirm")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"folder":"Care-Gaps"}
                                """))
                .andExpect(status().isConflict());
    }

    // ── POST /api/faxes/{trackingId}/reclassify ───────────────────────────────

    @Test
    void reclassify_nonFinalDoc_returns202() throws Exception {
        UUID tid = UUID.randomUUID();
        FaxDocumentEntity doc = buildDoc(tid);
        doc.setProcessingStatus("OCR");

        given(faxRepo.findByTrackingId(tid)).willReturn(Optional.of(doc));
        given(routingDecisionRepo.findTopByFaxDocumentIdOrderByDecidedAtDesc(doc.getId()))
                .willReturn(Optional.empty());
        given(routingDecisionRepo.findAllByFaxDocumentIdIn(any()))
                .willReturn(List.of());
        // Resolve to the fixture PDF that actually exists on disk
        java.nio.file.Path fixturePath = java.nio.file.Paths.get(
                "src", "test", "resources", "fixtures", "sample_pa.pdf").toAbsolutePath();
        given(fileStorageService.resolve(any())).willReturn(fixturePath);

        mvc.perform(post("/api/faxes/" + tid + "/reclassify"))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.trackingId").value(tid.toString()));
    }

    @Test
    void reclassify_alreadyFiled_returns409() throws Exception {
        UUID tid = UUID.randomUUID();
        FaxDocumentEntity doc = buildDoc(tid);
        RoutingDecisionEntity rd = buildRd(doc.getId(), "Care-Gaps", "CONFIRMED");
        rd.setFinalFolder("Care-Gaps");

        given(faxRepo.findByTrackingId(tid)).willReturn(Optional.of(doc));
        given(routingDecisionRepo.findTopByFaxDocumentIdOrderByDecidedAtDesc(doc.getId()))
                .willReturn(Optional.of(rd));

        mvc.perform(post("/api/faxes/" + tid + "/reclassify"))
                .andExpect(status().isConflict());
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private FaxDocumentEntity buildDoc(UUID trackingId) {
        FaxDocumentEntity e = new FaxDocumentEntity();
        e.setId(1L);
        e.setTrackingId(trackingId);
        e.setOriginalFileName("test.pdf");
        e.setStoredPath("incoming/test.pdf");
        e.setFileSizeBytes(1024L);
        e.setProcessingStatus("RECEIVED");
        e.setUploadSource("MANUAL_UPLOAD");
        e.setCreatedAt(OffsetDateTime.now());
        e.setUpdatedAt(OffsetDateTime.now());
        return e;
    }

    private RoutingDecisionEntity buildRd(Long faxDocId, String folder, String status) {
        RoutingDecisionEntity rd = new RoutingDecisionEntity();
        rd.setId(1L);
        rd.setFaxDocumentId(faxDocId);
        rd.setSuggestedFolder(folder);
        rd.setRoutingStatus(status);
        rd.setDecidedAt(OffsetDateTime.now());
        return rd;
    }
}
