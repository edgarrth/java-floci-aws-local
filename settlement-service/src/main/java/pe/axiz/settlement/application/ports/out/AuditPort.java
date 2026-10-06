package pe.axiz.settlement.application.ports.out;

import java.util.Optional;
import pe.axiz.settlement.domain.PaymentAudit;

public interface AuditPort {
  void record(PaymentAudit audit);

  Optional<PaymentAudit> find(String eventId);
}
