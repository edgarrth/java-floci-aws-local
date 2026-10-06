package pe.axiz.payment.adapters.out.http;

public final class TransientAcquirerException extends RuntimeException {
  public TransientAcquirerException(String message) {
    super(message);
  }

  public TransientAcquirerException(Throwable cause) {
    super("Falló la comunicación con el adquirente", cause);
  }
}
