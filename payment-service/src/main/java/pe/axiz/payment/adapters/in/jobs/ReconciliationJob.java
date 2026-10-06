package pe.axiz.payment.adapters.in.jobs;

import org.slf4j.*;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import pe.axiz.payment.application.ports.in.ProcessPaymentUseCase;
import pe.axiz.payment.application.ports.out.PaymentStore;

@Component
@ConditionalOnProperty(name = "axiz.jobs-enabled", havingValue = "true")
public class ReconciliationJob {
  private static final Logger LOG = LoggerFactory.getLogger(ReconciliationJob.class);
  private final PaymentStore store;
  private final ProcessPaymentUseCase service;

  public ReconciliationJob(PaymentStore store, ProcessPaymentUseCase service) {
    this.store = store;
    this.service = service;
  }

  @Scheduled(fixedDelay = 30000, initialDelay = 30000)
  public void reconcile() {
    for (var id : store.unresolved()) {
      try {
        service.reconcile(id);
      } catch (RuntimeException e) {
        LOG.warn("Se mantiene pendiente la conciliación del pago {}", id);
      }
    }
  }
}
