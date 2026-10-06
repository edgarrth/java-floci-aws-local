package pe.axiz.settlement.adapters.in.sqs;

import org.slf4j.*;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.core.env.Environment;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import pe.axiz.settlement.application.service.SettlementService;
import pe.axiz.settlement.domain.SettlementEvent;
import software.amazon.awssdk.services.sqs.SqsClient;
import tools.jackson.databind.ObjectMapper;

@Component
@ConditionalOnProperty(name = "axiz.jobs-enabled", havingValue = "true")
public class SettlementConsumer {
  private static final Logger LOG = LoggerFactory.getLogger(SettlementConsumer.class);
  private final SqsClient sqs;
  private final ObjectMapper json;
  private final SettlementService service;
  private final String url;

  public SettlementConsumer(
      SqsClient sqs, ObjectMapper json, SettlementService service, Environment e) {
    this.sqs = sqs;
    this.json = json;
    this.service = service;
    url = e.getRequiredProperty("axiz.queue-url");
  }

  @Scheduled(fixedDelay = 500, initialDelay = 3000)
  public void consume() {
    try {
      var batch =
          sqs.receiveMessage(
              b ->
                  b.queueUrl(url).maxNumberOfMessages(10).waitTimeSeconds(2).visibilityTimeout(30));
      for (var m : batch.messages())
        try {
          service.settle(json.readValue(m.body(), SettlementEvent.class));
          // La confirmación se realiza después del commit local, incluso si el comprobante sigue
          // pendiente.
          sqs.deleteMessage(b -> b.queueUrl(url).receiptHandle(m.receiptHandle()));
        } catch (RuntimeException ex) {
          LOG.warn("Se conservará el mensaje {} para reintento o DLQ", m.messageId());
        }
    } catch (RuntimeException ex) {
      LOG.warn("Se reintentará la lectura de SQS");
    }
  }
}
